from __future__ import annotations

import unittest

import httpx

from b_agent.hcx import (
    HcxAuthenticationError,
    HcxClient,
    HcxConfig,
    HcxResponseError,
    HcxTimeoutError,
    build_hcx_client_from_env,
    hcx_is_configured,
    load_hcx_config,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, error=None):
        self.status_code = status_code
        self.payload = payload
        self.error = error

    def json(self):
        if self.error is not None:
            raise self.error
        return self.payload


class FakeHttpClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        if self.error is not None:
            raise self.error
        return self.response


def _config():
    return HcxConfig(
        api_key="nv-test-secret",
        endpoint="https://clovastudio.stream.ntruss.com",
        model_name="HCX-005",
        timeout_sec=20.0,
        max_tokens=800,
    )


def _success_payload(content="근거 기반 답변 [structured:bond:B2:1]"):
    return {
        "status": {"code": "20000", "message": "OK"},
        "result": {
            "message": {"role": "assistant", "content": content},
            "finishReason": "stop",
        },
    }


class HcxClientTest(unittest.TestCase):
    def test_generate_calls_chat_completions_v3_without_exposing_key_in_body(self):
        http = FakeHttpClient(FakeResponse(payload=_success_payload()))
        client = HcxClient(_config(), http_client=http)

        answer = client.generate("검색 근거")

        self.assertEqual(answer, "근거 기반 답변 [structured:bond:B2:1]")
        call = http.calls[0]
        self.assertEqual(
            call["url"],
            "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-005",
        )
        self.assertEqual(call["headers"]["Authorization"], "Bearer nv-test-secret")
        self.assertTrue(call["headers"]["X-NCP-CLOVASTUDIO-REQUEST-ID"])
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertEqual(call["timeout"], 20.0)
        self.assertEqual(call["json"]["temperature"], 0.1)
        self.assertEqual(call["json"]["maxTokens"], 800)
        self.assertNotIn("nv-test-secret", str(call["json"]))
        self.assertIn("내부 추론 과정은 출력하지 마세요", call["json"]["messages"][0]["content"])

    def test_timeout_becomes_safe_typed_error(self):
        request = httpx.Request("POST", "https://clovastudio.stream.ntruss.com")
        http = FakeHttpClient(error=httpx.ReadTimeout("secret body", request=request))

        with self.assertRaisesRegex(HcxTimeoutError, "timed out") as caught:
            HcxClient(_config(), http_client=http).generate("검색 근거")

        self.assertNotIn("secret body", str(caught.exception))

    def test_authentication_failure_becomes_safe_typed_error(self):
        http = FakeHttpClient(FakeResponse(status_code=401, payload={"secret": "body"}))

        with self.assertRaisesRegex(HcxAuthenticationError, "authentication failed") as caught:
            HcxClient(_config(), http_client=http).generate("검색 근거")

        self.assertNotIn("secret", str(caught.exception))

    def test_empty_malformed_and_truncated_responses_are_rejected(self):
        cases = [
            FakeResponse(payload={"status": {"code": "20000"}, "result": {}}),
            FakeResponse(error=ValueError("not json")),
            FakeResponse(
                payload={
                    "status": {"code": "20000"},
                    "result": {
                        "message": {"role": "assistant", "content": "잘린 답변"},
                        "finishReason": "length",
                    },
                }
            ),
            FakeResponse(payload=_success_payload("   ")),
        ]

        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(HcxResponseError):
                    HcxClient(
                        _config(), http_client=FakeHttpClient(response)
                    ).generate("검색 근거")


class HcxConfigurationTest(unittest.TestCase):
    def test_environment_controls_endpoint_model_and_timeout_budget(self):
        config = load_hcx_config(
            {
                "CLOVA_API_KEY": "nv-key",
                "CLOVA_ENDPOINT": "https://clovastudio.stream.ntruss.com",
                "MODEL_NAME": "HCX-005",
                "REQUEST_TIMEOUT": "15",
                "HCX_TIMEOUT": "20",
                "HCX_MAX_TOKENS": "500",
            }
        )

        self.assertIsNotNone(config)
        self.assertEqual(config.timeout_sec, 5.0)
        self.assertEqual(config.max_tokens, 500)

    def test_missing_or_invalid_environment_disables_client_safely(self):
        self.assertIsNone(build_hcx_client_from_env({}))
        self.assertIsNone(
            build_hcx_client_from_env(
                {"CLOVA_API_KEY": "nv-key", "MODEL_NAME": "HCX-005"}
            )
        )
        self.assertFalse(
            hcx_is_configured(
                {
                    "CLOVA_API_KEY": "nv-key",
                    "CLOVA_ENDPOINT": "http://insecure.example.test",
                    "MODEL_NAME": "HCX-005",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
