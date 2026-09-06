"""Run with .[ontology-validation]; dedicated CI installs the required extra."""
import ast
from pathlib import Path
import unittest

try:
    from rdflib import Graph, Namespace
    from rdflib.namespace import OWL, RDF, RDFS, SH
    from owlrl import DeductiveClosure, OWLRL_Semantics
    import pyshacl  # noqa: F401
except ImportError:
    RDF_AVAILABLE = False
else:
    RDF_AVAILABLE = True

from scripts.validate_ontology import load_schema, validate_data

FP = Namespace("https://miraeasset.example/ontology/") if RDF_AVAILABLE else None
PREFIX = '''@prefix fp: <https://miraeasset.example/ontology/> .
@prefix ex: <https://example.test/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
'''
CHAIN = '''
ex:parent a fp:Organization ; fp:hasSubsidiary ex:child .
ex:child a fp:Issuer .
ex:stock a fp:Constituent ; fp:isIssuedBy ex:child .
ex:etf a fp:DomesticETF ; fp:productId "example" ; fp:currency "KRW" ; fp:holds ex:stock .
'''


@unittest.skipUnless(RDF_AVAILABLE, "Install .[ontology-validation] for RDF/SHACL tests")
class OntologySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema()

    def check(self, triples, expected=True):
        data = Graph().parse(data=PREFIX + triples, format="turtle")
        conforms, _, report = validate_data(data, self.schema)
        self.assertEqual(conforms, expected, report)
        return data

    def test_meta_and_valid_chain_query(self):
        self.check("")
        data = self.check(CHAIN)
        rows = list(data.query('''PREFIX fp: <https://miraeasset.example/ontology/>
            PREFIX ex: <https://example.test/>
            SELECT DISTINCT ?etf WHERE {
                ex:parent fp:hasSubsidiary ?company .
                ?security fp:isIssuedBy ?company .
                ?etf a fp:DomesticETF ; fp:holds ?security .
            }'''))
        self.assertEqual([str(row[0]) for row in rows], ["https://example.test/etf"])

    def test_company_is_not_security(self):
        for relation, expected in (("holds", False), ("hasExposureTo", True)):
            self.check(f'ex:f a fp:PublicFund ; fp:productId "F" ; fp:{relation} ex:c . ex:c a fp:Organization .', expected)

    def test_bond_invalid_values_individually(self):
        for value in ('fp:creditRating "AAAA"', 'fp:couponRate "abc"',
                      'fp:purchaseYield "확인불가"', 'fp:remainingDays -1',
                      'fp:creditRating "미등급"', 'fp:creditRating "AA0"'):
            with self.subTest(value=value):
                self.check('ex:b a fp:Bond ; fp:productId "B" ; ' + value + ' .', False)

    def test_bond_valid_negative_yield_and_zero_coupon(self):
        self.check('''ex:b a fp:Bond ; fp:productId "B" ; fp:creditRating "AA" ;
            fp:couponRate "0"^^xsd:decimal ; fp:purchaseYield "-0.5"^^xsd:decimal ;
            fp:remainingDays "0"^^xsd:nonNegativeInteger .''')

    def test_ratings_match_project_supported_codes(self):
        source = Path(__file__).resolve().parents[1] / "b_agent/postgres_gateway.py"
        tree = ast.parse(source.read_text())
        expected = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "_CREDIT_RATINGS" for t in n.targets))
        shape = next(n for n in self.schema.objects(FP.BondShape, SH.property)
                     if self.schema.value(n, SH.path) == FP.creditRating)
        actual = tuple(str(v) for v in self.schema.items(self.schema.value(shape, SH["in"])))
        self.assertEqual(actual, expected)
        for rating in expected:
            self.check(f'ex:b a fp:Bond ; fp:productId "B" ; fp:creditRating "{rating}" .')

    def test_risk_grade_range_type_and_count(self):
        for value, valid in (("1", True), ("6", True), ("7", False), ("0", False),
                             ('"높은위험(2등급)"', False), ("2, 6", False)):
            with self.subTest(value=value):
                self.check(f'ex:f a fp:PublicFund ; fp:productId "F" ; fp:riskGrade {value} .', valid)

    def test_all_scalar_numeric_metrics_reject_duplicates_and_text(self):
        groups = {
            'fp:PublicFund ; fp:productId "F"': ['riskGrade', 'netAssets', 'returnOneYearPct', 'returnOneMonth', 'returnThreeYears', 'returnFiveYears'],
            'fp:DomesticETF ; fp:productId "D" ; fp:currency "KRW"': ['netAssetsKRW', 'expenseRatio', 'volatility', 'leverageFactor', 'returnOneYearPct'],
            'fp:ForeignETF ; fp:productId "E" ; fp:ticker "E" ; fp:currency "USD"': ['nativeAUM', 'closingPrice', 'tradingVolume', 'returnOneYearPct'],
            'fp:Bond ; fp:productId "B"': ['couponRate', 'purchaseYield', 'remainingDays'],
        }
        for subject, properties in groups.items():
            for prop in properties:
                dtype = 'integer' if prop == 'riskGrade' else ('nonNegativeInteger' if prop in ('remainingDays', 'tradingVolume') else 'decimal')
                for value in (f'"2"^^xsd:{dtype}, "6"^^xsd:{dtype}', '"abc"'):
                    with self.subTest(prop=prop, value=value):
                        self.check(f'ex:p a {subject} ; fp:{prop} {value} .', False)

    def test_return_canonical_does_not_infer_wrong_product_type(self):
        for cls, other, extra in ((FP.PublicFund, FP.ETF, ''), (FP.DomesticETF, FP.PublicFund, '; fp:currency "KRW"')):
            data = self.check(f'ex:p a <{cls}> ; fp:productId "P" ; fp:returnOneYearPct "-2.5"^^xsd:decimal {extra} .')
            graph = self.schema + data
            # Explicit test of schema semantics only; production validation stays inference=none.
            DeductiveClosure(OWLRL_Semantics).expand(graph)
            self.assertNotIn((Namespace('https://example.test/').p, RDF.type, other), graph)
        for prop in ('oneYearReturn', 'returnOneYear'):
            self.check(f'ex:p a fp:PublicFund ; fp:productId "P" ; fp:{prop} "3"^^xsd:decimal .', False)

    def test_public_fund_and_etf_are_exclusive(self):
        for cls in ('ETF', 'DomesticETF', 'ForeignETF'):
            self.check(f'ex:p a fp:PublicFund, fp:{cls} ; fp:productId "P" ; fp:currency "KRW" ; fp:ticker "T" .', False)

    def test_original_disjoint_and_currency_constraints(self):
        self.check('ex:p a fp:Bond, fp:PublicFund ; fp:productId "P" .', False)
        self.check('ex:p a fp:Bond, fp:Organization ; fp:productId "P" .', False)
        self.check('ex:p a fp:DomesticETF, fp:ForeignETF ; fp:productId "P" ; fp:currency "KRW" ; fp:ticker "T" .', False)
        self.check('ex:p a fp:DomesticETF ; fp:productId "P" ; fp:currency "KRW", "USD" .', False)
        self.check('ex:p a fp:ForeignETF ; fp:productId "P" ; fp:ticker "T" ; fp:currency "usd" .', False)

    def test_missing_availability_and_duplicate_dates(self):
        self.check('ex:b a fp:Bond ; fp:productId "B" .')
        self.check('ex:b a fp:Bond ; fp:productId "B" ; fp:isAvailableForSale "unknown" .', False)
        self.check('ex:b a fp:Bond ; fp:productId "B" ; fp:asOfDate "2026-09-01"^^xsd:date, "2026-09-02"^^xsd:date .', False)

    def test_direct_subsidiary_relation_and_bridge(self):
        self.assertNotIn((FP.subsidiaryOf, RDF.type, OWL.TransitiveProperty), self.schema)
        self.assertIn((FP.Issuer, RDFS.subClassOf, FP.Organization), self.schema)
        self.assertIn((FP.isIssuedBy, OWL.inverseOf, FP.issues), self.schema)
        self.assertEqual(set(self.schema.objects(FP.holds, RDFS.range)), {FP.Security})


    def test_shared_risk_grade_for_bond_and_etfs(self):
        for cls, extra in (("Bond", ""), ("DomesticETF", '; fp:currency "KRW"'),
                           ("ForeignETF", '; fp:currency "USD" ; fp:ticker "T"')):
            for value, expected in (("2", True), ("7", False), ("2, 6", False), ('"높은위험(2등급)"', False)):
                with self.subTest(cls=cls, value=value):
                    self.check(f'ex:p a fp:{cls} ; fp:productId "P" ; fp:riskGrade {value} {extra} .', expected)
        self.assertEqual(set(self.schema.objects(FP.riskGrade, RDFS.domain)), {FP.FinancialProduct})
        graph = self.schema + self.check('ex:p a fp:Bond ; fp:productId "P" ; fp:riskGrade 2 .')
        DeductiveClosure(OWLRL_Semantics).expand(graph)
        self.assertNotIn((Namespace('https://example.test/').p, RDF.type, FP.PublicFund), graph)

    def guide_data(self):
        path = Path(__file__).parent / "fixtures/ontology/guide_examples.ttl"
        data = Graph().parse(path, format="turtle")
        conforms, _, report = validate_data(data, self.schema)
        self.assertTrue(conforms, report)
        return data

    def test_guide_queries_listing_and_history(self):
        data = self.guide_data()
        prefix = "PREFIX fp: <https://miraeasset.example/ontology/> PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
        rows = list(data.query(prefix + '''SELECT ?etf WHERE {
            ?etf a fp:ForeignETF ; fp:hasAssetType fp:asset_equity ; fp:isListedOn ?market .
            ?market fp:marketCountryCode "US" . }'''))
        self.assertEqual([str(r[0]) for r in rows], ['https://example.test/etf'])
        # A listing in the US must not silently change an investment region in China.
        self.assertNotIn((Namespace('https://example.test/').etf, FP.hasInvestmentRegion, FP.region_US), data)
        rows = list(data.query(prefix + '''SELECT ?event WHERE {
            ?event a fp:ThemeAssociation ; fp:linkedOn ?date ; fp:associationTheme fp:theme_space_aerospace .
            FILTER(?date >= "2026-01-11"^^xsd:date && ?date <= "2026-07-11"^^xsd:date)
        }'''))
        self.assertEqual([str(r[0]) for r in rows], ['https://example.test/recent'])
        rows = list(data.query(prefix + '''SELECT ?etf WHERE {
            ?parent fp:hasSubsidiary ?issuer . ?stock fp:isIssuedBy ?issuer .
            ?listing a fp:ListingObservation ; fp:listedProduct ?stock ;
                fp:asOfDate "2026-07-11"^^xsd:date .
            ?etf fp:holds ?stock . }'''))
        self.assertEqual([str(r[0]) for r in rows], ['https://example.test/etf'])

    def test_new_records_require_dates_evidence_and_correct_targets(self):
        ex = Namespace('https://example.test/')
        original = self.guide_data()
        for subject, prop in ((ex.recent, FP.linkedOn), (ex.recent, FP.supportedBy),
                              (ex.recent, FP.concernsProduct), (ex.listing, FP.asOfDate),
                              (ex.listing, FP.supportedBy), (ex.doc, FP.sourceIdentifier)):
            with self.subTest(subject=subject, prop=prop):
                data = original + Graph()
                data.remove((subject, prop, None))
                self.assertFalse(validate_data(data, self.schema)[0])
        for subject, prop, value in ((ex.listing, FP.listedProduct, ex.issuer),
                                     (ex.recent, FP.associationTheme, ex.issuer),
                                     (ex.etf, FP.hasAssetType, ex.issuer),
                                     (ex.etf, FP.describedBy, ex.issuer)):
            with self.subTest(prop=prop):
                data = original + Graph()
                data.set((subject, prop, value))
                self.assertFalse(validate_data(data, self.schema)[0])


if __name__ == '__main__':
    unittest.main()
