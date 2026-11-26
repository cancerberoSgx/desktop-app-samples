from app.models import ProjectGroup
from app.project_list import project_default_ascending, project_header_label, sort_projects


def group(name, count, last, providers=("claude-code",)):
    return ProjectGroup(path=f"/x/{name}", name=name, providers=list(providers), project_ids=[1],
                        conversation_count=count, last_activity=last)


GROUPS = [group("beta", 9, "2026-03-01"), group("Alpha", 10, None), group("gamma", 2, "2026-05-01")]


def names(groups):
    return [g.name for g in groups]


def test_sort_by_name_case_insensitive():
    assert names(sort_projects(GROUPS, 0, True)) == ["Alpha", "beta", "gamma"]
    assert names(sort_projects(GROUPS, 0, False)) == ["gamma", "beta", "Alpha"]


def test_sort_by_conversation_count_is_numeric():
    assert names(sort_projects(GROUPS, 1, False)) == ["Alpha", "beta", "gamma"]


def test_sort_by_last_activity_missing_is_oldest():
    assert names(sort_projects(GROUPS, 2, False)) == ["gamma", "beta", "Alpha"]


def test_default_direction_and_header_arrow():
    assert project_default_ascending(0) and project_default_ascending(3)
    assert not project_default_ascending(1) and not project_default_ascending(2)
    assert project_header_label(2, 2, False) == "Last activity ▼"
    assert project_header_label(0, 2, False) == "Project"
