from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[1]
        / "job-market-analysis"
        / "scripts"
        / "analyze_fine_role_skills.py"
    )
    spec = importlib.util.spec_from_file_location("fine_role_skills", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def job(
    *,
    job_id: str,
    title: str,
    cluster: str,
    industry: str = "General software / other",
    must: str = "",
    nice: str = "",
    responsibility: str = "",
    stack: str = "",
    shortlist: str = "0",
) -> dict[str, str]:
    return {
        "job_id": job_id,
        "title": title,
        "company": "Fictional Systems GmbH",
        "role_cluster": cluster,
        "industry": industry,
        "skills_must": must,
        "skills_nice": nice,
        "skills_responsibility": responsibility,
        "skills_stack": stack,
        "notion_shortlist_snapshot": shortlist,
        "description_present": "1",
        "german_requirement": "Not explicit",
    }


def test_cross_family_role_gets_one_primary_category_per_family() -> None:
    row = job(
        job_id="robot-1",
        title="C++ Robotics Perception Engineer",
        cluster="Robotics & Autonomous Systems",
        must="C++|Computer Vision|ROS / ROS2",
    )

    classifications = MODULE.classify_row(row)

    assert [(item.family, item.category) for item in classifications] == [
        ("AI", "Computer Vision / 3D Perception"),
        ("C++", "C++ Robotics / Autonomous Systems"),
    ]


def test_cpp_test_title_takes_priority_over_embedded_cluster() -> None:
    row = job(
        job_id="test-1",
        title="C++ Test Automation Engineer",
        cluster="Embedded / Firmware & Industrial",
        must="C++|Testing",
        nice="CAN / Serial",
    )

    classification = MODULE.classify_cpp(row)

    assert classification is not None
    assert classification.category == "C++ Test Automation / Verification"


def test_german_compound_test_title_is_classified_as_cpp_verification() -> None:
    row = job(
        job_id="test-de",
        title="C++ Testingenieur Signalverarbeitung",
        cluster="Embedded / Firmware & Industrial",
        must="C++|Testing",
    )

    classification = MODULE.classify_cpp(row)

    assert classification is not None
    assert classification.category == "C++ Test Automation / Verification"


def test_ai_platform_title_takes_priority_over_general_ml_skills() -> None:
    row = job(
        job_id="ai-1",
        title="AI Platform Engineer",
        cluster="AI / Machine Learning Engineering",
        must="Python|MLOps",
        stack="PyTorch|Kubernetes",
    )

    classification = MODULE.classify_ai(row)

    assert classification is not None
    assert classification.category == "AI Platform / MLOps / Inference"


def test_ai_structured_skill_fallback_is_supported() -> None:
    row = job(
        job_id="ai-2",
        title="Software Engineer",
        cluster="AI / Machine Learning Engineering",
        must="Python|PyTorch",
    )

    classification = MODULE.classify_ai(row)

    assert classification is not None
    assert classification.category == "ML Engineering / Data Science"


def test_ai_infra_without_cpp_evidence_is_not_a_cpp_role() -> None:
    row = job(
        job_id="ai-platform-only",
        title="AI Platform Engineer",
        cluster="AI Infrastructure / Inference",
        must="Python|MLOps",
        stack="Kubernetes",
    )

    assert MODULE.classify_cpp(row) is None


def test_cpp_inference_role_is_kept_as_cross_family() -> None:
    row = job(
        job_id="cpp-inference",
        title="C++ Inference Engineer",
        cluster="AI Infrastructure / Inference",
        must="C++|Inference / Model Serving",
    )

    classification = MODULE.classify_cpp(row)

    assert classification is not None
    assert classification.category == "C++ AI Systems / Inference"


def test_trading_cluster_without_cpp_evidence_is_not_a_cpp_role() -> None:
    row = job(
        job_id="python-quant",
        title="Quantitative Researcher",
        cluster="Trading / Financial Systems",
        must="Python",
    )

    assert MODULE.classify_cpp(row) is None


def test_skill_aggregation_preserves_requirement_types_and_exclusivity() -> None:
    rows = [
        job(
            job_id="cpp-1",
            title="C++ Backend Engineer",
            cluster="C++ Backend & Distributed Systems",
            must="C++|Linux",
            nice="Docker",
            shortlist="1",
        ),
        job(
            job_id="cpp-2",
            title="C++ Distributed Systems Engineer",
            cluster="C++ Backend & Distributed Systems",
            must="C++",
            responsibility="Linux",
            stack="Docker",
        ),
    ]

    analysis = MODULE.analyze(rows)
    mappings = analysis["mappings"]
    skills = analysis["skills"]

    assert len(mappings) == 2
    assert len({(row["job_id"], row["family"]) for row in mappings}) == 2
    linux = next(
        row
        for row in skills
        if row["category"] == "C++ Backend / Distributed Systems" and row["skill"] == "Linux"
    )
    assert linux["skill_count"] == 2
    assert linux["must_count"] == 1
    assert linux["responsibility_count"] == 1
    assert linux["skill_share_pct"] == 100.0
