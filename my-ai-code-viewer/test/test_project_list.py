from app.models import ProjectGroup
from app.project_list import format_project_cost, project_default_ascending, project_header_label, sort_projects


def group(name, count, last, providers=("claude-code",), tokens=0, cost=None, unknown=0):
    return ProjectGroup(path=f"/x/{name}", name=name, providers=list(providers), project_ids=[1],
                        conversation_count=count, last_activity=last, total_tokens=tokens, cost_usd=cost,
                        cost_unknown=unknown)


GROUPS = [group("beta", 9, "2026-03-01", tokens=5_000, cost=0.5),
          group("Alpha", 10, None, tokens=20, unknown=2),
          group("gamma", 2, "2026-05-01", tokens=1_000_000, cost=12.0, unknown=1)]


def names(groups):
    return [g.name for g in groups]


def test_sort_by_name_case_insensitive():
    assert names(sort_projects(GROUPS, 0, True)) == ["Alpha", "beta", "gamma"]
    assert names(sort_projects(GROUPS, 0, False)) == ["gamma", "beta", "Alpha"]


def test_sort_by_conversation_count_is_numeric():
    assert names(sort_projects(GROUPS, 1, False)) == ["Alpha", "beta", "gamma"]


def test_sort_by_last_activity_missing_is_oldest():
    assert names(sort_projects(GROUPS, 2, False)) == ["gamma", "beta", "Alpha"]


def test_sort_by_tokens_and_cost():
    assert names(sort_projects(GROUPS, 3, False)) == ["gamma", "beta", "Alpha"]
    assert names(sort_projects(GROUPS, 4, False)) == ["gamma", "beta", "Alpha"]  # unknown cost is cheapest
    assert names(sort_projects(GROUPS, 4, True)) == ["Alpha", "beta", "gamma"]


def test_format_project_cost():
    assert [format_project_cost(g) for g in GROUPS] == ["$0.50", "?", "$12.00*"]
    assert format_project_cost(group("empty", 0, None)) == ""


def test_default_direction_and_header_arrow():
    assert project_default_ascending(0) and project_default_ascending(5)
    assert not any(project_default_ascending(c) for c in (1, 2, 3, 4))
    assert project_header_label(2, 2, False) == "Last activity ▼"
    assert project_header_label(0, 2, False) == "Project"
