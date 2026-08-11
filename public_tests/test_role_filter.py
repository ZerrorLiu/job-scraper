from job_scraper.domain.policies import TargetRule
from job_scraper.pipeline.role_filter import text_matches_target


def test_target_role_matches_runtime_tuple_keyword_groups() -> None:
    rule = TargetRule(
        name="ai_engineering",
        keyword_groups=(
            ("ai", "artificial intelligence", "genai", "llm"),
            ("engineer", "developer"),
        ),
        match_scope="title",
    )

    assert text_matches_target(
        "GenAI Engineer",
        "",
        [],
        "title",
        target_rules=[rule],
    )
    assert not text_matches_target(
        "GenAI Product Manager",
        "",
        [],
        "title",
        target_rules=[rule],
    )
