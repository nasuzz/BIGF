// Run with Node and @electric-sql/pglite; see docs/STRUCTURED_QUERY_CONTRACT.md.
// Uses the actual canonical table definitions, migration and generated gateway SQL.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
const require = createRequire(import.meta.url);
const { PGlite } = require(process.env.PGLITE_MODULE_PATH || '@electric-sql/pglite');
const root = new URL('../', import.meta.url);
const canonical = readFileSync(new URL('init/01_schema.sql', root), 'utf8');
const migration = readFileSync(new URL('migrations/V20260906_005__add_product_sale_availability.sql', root), 'utf8');
const db = new PGlite();
let checks = 0;
const check = (actual, expected, label) => { assert.deepEqual(actual, expected, label); checks++; };
try {
  await db.exec('CREATE SCHEMA core; CREATE SCHEMA raw; CREATE SCHEMA meta; CREATE SCHEMA search;');
  for (const name of ['core.product', 'core.fund_class', 'core.domestic_etp', 'core.overseas_etp',
                      'raw.source_row', 'meta.dataset_snapshot', 'meta.value_provenance',
                      'meta.data_version', 'meta.data_version_snapshot']) {
    const start = canonical.indexOf(`CREATE TABLE ${name} (`);
    assert.ok(start >= 0);
    await db.exec(canonical.slice(start, canonical.indexOf('\n);', start) + 3));
  }
  const viewStart = canonical.indexOf('CREATE VIEW search.product_metrics AS');
  await db.exec(canonical.slice(viewStart, canonical.indexOf(';', viewStart) + 1));
  await db.exec(migration);
  await db.exec(migration); // Re-applying the view definition must be safe.
  await db.exec("INSERT INTO meta.data_version VALUES (1, 'data_v1', 'ACTIVE', NULL, now(), now()), (2, 'old', 'RETIRED', NULL, now(), NULL)");

  async function insert(id, type, payload, {dataset, currency='QA', stored=null, active=true}={}) {
    dataset ||= {DOMESTIC_ETP:'DOMESTIC_ETP', OVERSEAS_ETP:'OVERSEAS_ETP', PUBLIC_FUND_CLASS:'PUBLIC_FUND'}[type];
    await db.query(`INSERT INTO meta.dataset_snapshot (snapshot_id,dataset_code,source_file_name,file_sha256,data_as_of_date)
                    VALUES ($1,$2,'master.xlsx','hash','2026-08-22')`, [id,dataset]);
    await db.query(`INSERT INTO raw.source_row (raw_row_id,snapshot_id,dataset_code,source_sheet,source_row_number,payload,row_hash)
                    VALUES ($1,$1,$2,'products',1,$3,'hash')`, [id,dataset,JSON.stringify(payload)]);
    await db.query(`INSERT INTO core.product (product_id,snapshot_id,raw_row_id,product_type,source_product_key,canonical_name,currency_code)
                    VALUES ($1,$1,$1,$2,($1::bigint)::text,$3,$4)`, [id,type,`테스트상품${id}`,currency]);
    await db.query('INSERT INTO meta.data_version_snapshot VALUES ($1,$2,$3)', [active ? 1 : 2,id,dataset]);
    if (type === 'PUBLIC_FUND_CLASS') {
      await db.query('INSERT INTO core.fund_class (product_id,sale_status,net_asset_amount) VALUES ($1,$2,$1::bigint)', [id,stored]);
    } else {
      const table = type === 'DOMESTIC_ETP' ? 'domestic_etp' : 'overseas_etp';
      await db.query(`INSERT INTO core.${table} (product_id,aum) VALUES ($1,$1::bigint)`, [id]);
    }
    return (await db.query('SELECT * FROM core.v_product_sale_availability WHERE product_id=$1', [id])).rows[0];
  }

  const etfCases = [
    [{pd_sale_yn:1,pd_tr_yn:0},true,'MASTER_AVAILABLE'],
    [{pd_sale_yn:'1.0',pd_tr_yn:'0.0'},true,'MASTER_AVAILABLE'],
    [{pd_sale_yn:1,pd_tr_yn:1},false,'TRADING_SUSPENDED'],
    [{pd_sale_yn:0,pd_tr_yn:0},false,'SALE_FLAG_OFF'],
    [{pd_sale_yn:0},false,'SALE_FLAG_OFF'],
    [{pd_tr_yn:1},false,'TRADING_SUSPENDED'],
    [{pd_sale_yn:1},null,'UNKNOWN_OR_MISSING'],
    [{pd_tr_yn:0},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:'Y',pd_tr_yn:1},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:1,pd_tr_yn:'N'},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:1,pd_tr_yn:'Y'},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:true,pd_tr_yn:false},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:' ',pd_tr_yn:null},null,'UNKNOWN_OR_MISSING'],
    [{pd_sale_yn:' 1 ',pd_tr_yn:' 0.0 '},true,'MASTER_AVAILABLE'],
  ];
  let id = 0;
  for (const [type,dataset] of [['DOMESTIC_ETP','DOMESTIC_ETP'],['DOMESTIC_ETP','PREF01N001'],
                                ['OVERSEAS_ETP','OVERSEAS_ETP'],['OVERSEAS_ETP','PREF02N001']]) {
    for (const [payload,expected,reason] of etfCases) {
      const row = await insert(++id,type,payload,{dataset});
      check(row.sale_available,expected,`${dataset} ${JSON.stringify(payload)}`);
      check(row.sale_available_reason,reason);
      check(row.sale_available_scope,'MASTER_STATUS_ONLY');
      check(row.status_basis_date,'2026-08-22');
    }
  }
  const fundBase = {prvo_pbff_desc:'공모',sale_yn:'판매중',thco_sale_yn:'Y'};
  const fundCases = [
    [fundBase,true,'MASTER_AVAILABLE'],
    [{...fundBase,sale_yn:'판매완료'},false,'SALE_CLOSED'],
    [{...fundBase,thco_sale_yn:null},null,'UNKNOWN_OR_MISSING'],
    [{...fundBase,thco_sale_yn:'N'},null,'UNKNOWN_OR_MISSING'],
    [{...fundBase,thco_sale_yn:'X'},null,'UNKNOWN_OR_MISSING'],
    [{...fundBase,sale_yn:null},null,'UNKNOWN_OR_MISSING'],
    [{...fundBase,sale_yn:'X'},null,'UNKNOWN_OR_MISSING'],
    [{...fundBase,prvo_pbff_desc:'사모'},false,'NOT_PUBLIC_OFFERING'],
    [{...fundBase,sale_status:'판매완료'},null,'SALE_STATUS_CONFLICT'],
    [{...fundBase,sale_status:'X'},null,'SALE_STATUS_CONFLICT'],
    [fundBase,null,'SALE_STATUS_CONFLICT','판매완료'],
    [fundBase,true,'MASTER_AVAILABLE','판매중'],
  ];
  for (const dataset of ['PUBLIC_FUND','PRFD01N001']) {
    for (const [payload,expected,reason,stored] of fundCases) {
      const row = await insert(++id,'PUBLIC_FUND_CLASS',payload,{dataset,stored});
      check(row.sale_available,expected);
      check(row.sale_available_reason,reason);
    }
  }
  check((await insert(++id,'DOMESTIC_ETP',etfCases[0][0],{dataset:'PREF02N001'})).sale_available,null);
  const dated = await insert(++id,'DOMESTIC_ETP',{...etfCases[0][0],du_upt_dt:'20260820'});
  check(dated.status_basis_date,'20260820');
  await db.query(`INSERT INTO meta.value_provenance (provenance_id,snapshot_id,raw_row_id,field_name,was_missing,fill_type)
                  VALUES (1,$1,$1,'pd_tr_yn',true,'estimated')`, [id]);
  check((await db.query('SELECT sale_available FROM core.v_product_sale_availability WHERE product_id=$1',[id])).rows[0].sale_available,null);

  for (const [pid,payload,currency,active] of [
    [1001,etfCases[0][0],'GATEWAY',true], [1002,etfCases[0][0],'GATEWAY',true],
    [1003,etfCases[0][0],'GATEWAY',true], [1004,etfCases[2][0],'GATEWAY',true],
    [1005,{},'GATEWAY',true], [1000,etfCases[0][0],'GATEWAY',false],
    [1100,{},'UNKNOWN',true], [1200,etfCases[2][0],'FALSEONLY',true],
  ]) await insert(pid,'DOMESTIC_ETP',payload,{currency,active});

  const python = spawnSync(process.env.SALE_TEST_PYTHON || 'python3', ['-c', `
import json
from b_agent.postgres_gateway import PostgresDataGateway
from b_agent.models import Capability, Evidence
class Capture(PostgresDataGateway):
    def _query(self, sql, params):
        self.call = {'sql': sql, 'params': params}
        return []
gateway = Capture(None)
out = {}
for name,currency,op,value,upstream in [
    ('true','GATEWAY','eq',True,{}), ('false','GATEWAY','eq',False,{}),
    ('not_false','GATEWAY','ne',False,{}), ('unknown','UNKNOWN','eq',True,{}),
    ('false_only','FALSEONLY','eq',True,{}), ('empty','EMPTY','eq',True,{}),
    ('scoped','GATEWAY','eq',True,{'identity':[Evidence(evidence_id='id',capability=Capability.IDENTITY_SEARCH,source_id='id',content='',product_id='1001')]}),
]:
    query={'product_types':['domestic_etf'],'sorts':[{'field':'net_assets','direction':'asc'}],
           'filters':[{'field':'sale_available','operator':op,'value':value},
                      {'field':'currency','operator':'eq','value':currency}]}
    gateway.retrieve(Capability.STRUCTURED_SEARCH,query,upstream,2)
    out[name]=gateway.call
print(json.dumps(out))
`], {cwd:root,encoding:'utf8'});
  assert.equal(python.status,0,python.stderr);
  const queries = JSON.parse(python.stdout);
  const results = {};
  for (const [name,{sql,params}] of Object.entries(queries)) {
    let index = 0;
    results[name] = (await db.query(sql.replace(/%s/g,()=>`$${++index}`),params)).rows;
    check(index,params.length);
  }
  check(results.true.map(r=>Number(r.product_id)),[1001,1002]); // before LIMIT, inactive 1000 excluded
  check(Number(results.true[0]._total_hits),3);
  check(Number(results.true[0]._sale_population),5);
  check(Number(results.true[0]._missing_sale_available),1);
  check(results.false.map(r=>Number(r.product_id)),[1004]);
  check(results.not_false.map(r=>Number(r.product_id)),[1001,1002]);
  check(results.scoped.map(r=>Number(r.product_id)),[1001]);
  check(Number(results.scoped[0]._sale_population),1);
  check(Number(results.scoped[0]._missing_sale_available),0);
  for (const [name,population,missing] of [['unknown',1,1],['false_only',1,0],['empty',0,0]]) {
    check(results[name].length,1); // Aggregate sentinel survives empty matches.
    check(results[name][0].product_id,null);
    check(Number(results[name][0]._total_hits),0);
    check(Number(results[name][0]._sale_population),population);
    check(Number(results[name][0]._missing_sale_available),missing);
  }
  console.log(`PASS: ${checks} PostgreSQL assertions (A's matrix, migration, ACTIVE scoping, gateway SQL).`);
} finally { await db.close(); }
