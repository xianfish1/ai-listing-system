from scripts.browser_use_adaptive_executor import BrowserUseAdaptiveExecutor


def test_takeover_prompt_includes_the_configured_cnsc_shop_identities() -> None:
    executor = object.__new__(BrowserUseAdaptiveExecutor)

    task_description = executor._format_task_for_ai(
        {
            "store": {
                "target_site_stores": [
                    {"site_code": "TW", "store_name": "海宁TW", "store_id": "59760739"},
                    {"site_code": "PH", "store_name": "海宁PH1", "store_id": "2021483750"},
                ],
            },
        },
    )

    assert "CNSC 目标店铺: TW / 海宁TW / 59760739; PH / 海宁PH1 / 2021483750" in task_description
