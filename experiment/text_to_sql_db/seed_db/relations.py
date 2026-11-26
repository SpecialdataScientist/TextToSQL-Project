"""Explicit key families used by the sample generator and database verifier."""

from dataclasses import dataclass

from .schema import Table


@dataclass(frozen=True)
class Relation:
    child: str
    child_columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]


PARENTS = {
    "MIT_ID": ("EMPLOYEE_DIRECTORY", "MIT_ID"),
    "RESPONSIBLE_FACULTY_MIT_ID": ("EMPLOYEE_DIRECTORY", "MIT_ID"),
    "MOIRA_LIST_MEMBER_MIT_ID": ("EMPLOYEE_DIRECTORY", "MIT_ID"),
    "HR_ORG_UNIT_ID": ("HR_ORG_UNIT", "HR_ORG_UNIT_ID"),
    "DLC_KEY": ("SIS_DEPARTMENT", "DLC_KEY"),
    "DEPARTMENT_CODE": ("SIS_DEPARTMENT", "DEPARTMENT_CODE"),
    "DEPARTMENT_NUMBER": ("SIS_DEPARTMENT", "DEPARTMENT_CODE"),
    "OFFER_DEPT_CODE": ("SIS_DEPARTMENT", "DEPARTMENT_CODE"),
    "DEPT_CODE": ("SIS_DEPARTMENT", "DEPARTMENT_CODE"),
    "TERM_CODE": ("ACADEMIC_TERMS", "TERM_CODE"),
    "SUBJECT_SUMMARY_KEY": ("SUBJECT_SUMMARY", "SUBJECT_SUMMARY_KEY"),
    "SUBJECT_OFFERED_SUMMARY_KEY": ("SUBJECT_OFFERED_SUMMARY", "SUBJECT_OFFERED_SUMMARY_KEY"),
    "SUBJECT_GROUPING_KEY": ("SUBJECT_GROUPING", "SUBJECT_GROUPING_KEY"),
    "FCLT_BUILDING_KEY": ("FCLT_BUILDING", "FCLT_BUILDING_KEY"),
    "BUILDING_KEY": ("BUILDINGS", "BUILDING_KEY"),
    "FCLT_FLOOR_KEY": ("FCLT_FLOOR", "FCLT_FLOOR_KEY"),
    "FLOOR_KEY": ("SPACE_FLOOR", "FLOOR_KEY"),
    "FCLT_ROOM_KEY": ("FCLT_ROOMS", "FCLT_ROOM_KEY"),
    "FCLT_ORGANIZATION_KEY": ("FCLT_ORGANIZATION", "FCLT_ORGANIZATION_KEY"),
    "FCLT_MAJOR_USE_KEY": ("FCLT_MAJOR_USE", "FCLT_MAJOR_USE_KEY"),
    "SPACE_UNIT_KEY": ("SPACE_UNIT", "SPACE_UNIT_KEY"),
    "SPACE_USAGE_KEY": ("SPACE_USAGE", "SPACE_USAGE_KEY"),
    "TIP_MATERIAL_KEY": ("TIP_MATERIAL", "TIP_MATERIAL_KEY"),
    "TIP_SUBJECT_OFFERED_KEY": ("TIP_SUBJECT_OFFERED", "TIP_SUBJECT_OFFERED_KEY"),
    "TIP_MATERIAL_STATUS_KEY": ("TIP_MATERIAL_STATUS", "TIP_MATERIAL_STATUS_KEY"),
    "LIBRARY_RESERVE_CATALOG_KEY": ("LIBRARY_RESERVE_CATALOG", "LIBRARY_RESERVE_CATALOG_KEY"),
    "LIBRARY_SUBJECT_OFFERED_KEY": ("LIBRARY_SUBJECT_OFFERED", "LIBRARY_SUBJECT_OFFERED_KEY"),
    "LIBRARY_COURSE_INSTRUCTOR_KEY": ("LIBRARY_COURSE_INSTRUCTOR", "LIBRARY_COURSE_INSTRUCTOR_KEY"),
    "LIBRARY_MATERIAL_STATUS_KEY": ("LIBRARY_MATERIAL_STATUS", "LIBRARY_MATERIAL_STATUS_KEY"),
    "IAP_SUBJECT_CATEGORY_KEY": ("IAP_SUBJECT_CATEGORY", "IAP_SUBJECT_CATEGORY_KEY"),
    "IAP_SUBJECT_SPONSOR_KEY": ("IAP_SUBJECT_SPONSOR", "IAP_SUBJECT_SPONSOR_KEY"),
    "IAP_SUBJECT_SESSION_KEY": ("IAP_SUBJECT_SESSION", "IAP_SUBJECT_SESSION_KEY"),
    "IAP_SUBJECT_PERSON_KEY": ("IAP_SUBJECT_PERSON", "IAP_SUBJECT_PERSON_KEY"),
    "MOIRA_LIST_KEY": ("MOIRA_LIST", "MOIRA_LIST_KEY"),
    "MOIRA_LIST_OWNER_KEY": ("MOIRA_LIST_OWNER", "MOIRA_LIST_OWNER_KEY"),
    "TIME_MONTH_KEY": ("TIME_MONTH", "TIME_MONTH_KEY"),
}


def relationships(tables: tuple[Table, ...]) -> tuple[tuple[Relation, ...], tuple[str, ...]]:
    catalog = {t.name: {c.name: c.source_type for c in t.columns} for t in tables}
    relations, excluded = [], []
    for table in tables:
        for column in table.columns:
            parent = PARENTS.get(column.name)
            if parent is None or parent[0] == table.name or parent[0] not in catalog:
                continue
            target_type = catalog[parent[0]].get(parent[1])
            if target_type is None:
                continue
            if target_type != column.source_type:
                excluded.append(f"{table.name}.{column.name} ({column.source_type}) -> {parent[0]}.{parent[1]} ({target_type})")
                continue
            relations.append(Relation(table.name, (column.name,), parent[0], (parent[1],)))
        if table.name != "SUBJECT_SUMMARY" and {"TERM_CODE", "SUBJECT_ID"} <= catalog[table.name].keys() and "SUBJECT_SUMMARY" in catalog:
            relations.append(Relation(table.name, ("TERM_CODE", "SUBJECT_ID"), "SUBJECT_SUMMARY", ("TERM_CODE", "SUBJECT_ID")))
    if "FCLT_FLOOR" in catalog:
        for child in ("FCLT_ROOMS", "FCLT_ROOMS_HIST"):
            if child in catalog:
                relations.append(Relation(child, ("FCLT_BUILDING_KEY", "FCLT_FLOOR_KEY"),
                                          "FCLT_FLOOR", ("FCLT_BUILDING_KEY", "FCLT_FLOOR_KEY")))
    return tuple(relations), tuple(excluded)
