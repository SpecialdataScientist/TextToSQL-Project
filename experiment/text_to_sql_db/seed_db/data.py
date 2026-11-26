"""Deterministic domain values shared by related tables.

The source has no foreign keys. Relationships below are explicit sample-data
contracts; equal-looking names alone are not treated as foreign keys.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import random

from .schema import Column, SeedError, Table


SNAPSHOT = datetime(2026, 1, 15, 12)
DEPARTMENT_TABLES = {
    "SIS_DEPARTMENT", "STUDENT_DEPARTMENT", "SIS_SUBJECT_CODE", "SIS_COURSE_DESCRIPTION",
    "HR_ORG_UNIT", "HR_ORG_UNIT_NEW", "FCLT_ORGANIZATION", "FCLT_ORGANIZATION_HIST",
    "FAC_ORGANIZATION", "FCLT_ORG_DLC_KEY", "MASTER_DEPT_HIERARCHY",
    "MASTER_DEPT_HIERARCHY_LINKS", "MASTER_DEPT_DCODE_PARENT", "SPACE_UNIT", "SPACE_UNIT2",
}
PERSON_TABLES = {
    "EMPLOYEE_DIRECTORY", "DRUPAL_EMPLOYEE_DIRECTORY", "OPA_PERSON_CURRENT", "SE_PERSON",
    "WAREHOUSE_USERS", "HR_FACULTY_ROSTER", "MIT_STUDENT_DIRECTORY", "PERSON_AUTH_AREA", "ROLES_FIN_PA",
}
TERM_TABLES = {"ACADEMIC_TERMS", "ACADEMIC_TERMS_ALL", "ACADEMIC_TERM_PARAMETER"}
ROOM_TABLES = {"FCLT_ROOMS", "FCLT_ROOMS_HIST", "FAC_ROOMS", "SPACE_DETAIL", "ZPM_ROOMS_LOAD"}
FLOOR_TABLES = {"FCLT_FLOOR", "FCLT_FLOOR_HIST", "FAC_FLOOR", "SPACE_FLOOR"}
DEPARTMENTS = ("Computer Science", "Mathematics", "Physics", "Chemistry", "Biology",
               "Economics", "Architecture", "Mechanical Engineering", "History", "Management")
SUBJECTS = ("Introduction to Programming", "Linear Algebra", "Classical Mechanics",
            "Organic Chemistry", "Molecular Biology", "Microeconomics", "Urban Design",
            "Thermodynamics", "World History", "Business Analytics")


def _token(prefix: str, index: int) -> str:
    return f"{prefix}{index + 1:04d}"


def _random(seed: int, table: str, column: str, index: int) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{table}:{column}:{index}".encode()).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def _term(index: int) -> dict:
    year = 2026 + index // 4
    season = index % 4
    start = datetime(year, (1, 2, 6, 9)[season], 1)
    end = start + timedelta(days=(28, 100, 70, 100)[season])
    return dict(code=f"{year}{('20', '30', '00', '10')[season]}",
                title=f"{year} {('IAP', 'Spring', 'Summer', 'Fall')[season]}",
                year=str(year), start=start, end=end)


class Generator:
    def __init__(self, rows: int = 50, seed: int = 42):
        if rows < 1:
            raise SeedError("SEED_ROWS는 1 이상이어야 합니다.")
        self.rows = rows
        self.seed = seed
        rng = random.Random(seed)
        first = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Robin", "Sam", "Jamie", "Avery", "Riley"]
        last = ["Kim", "Lee", "Park", "Chen", "Brown", "Smith", "Garcia", "Patel", "Wilson", "Davis"]
        combinations = [(a, b) for a in first for b in last]
        rng.shuffle(combinations)
        self.people = [combinations[i % len(combinations)] for i in range(rows)]

    def _values(self, table: str, i: int) -> dict:
        n = self.rows
        dp, pp, bp, fp = min(n, 10), min(n, 15), min(n, 10), min(n, 20)
        d = i if table in DEPARTMENT_TABLES else i % dp
        p = i if table in PERSON_TABLES else i % pp
        t = i if table in TERM_TABLES else i % min(n, 8)
        f = i % fp if table in ROOM_TABLES else i
        b = f % bp if table in ROOM_TABLES | FLOOR_TABLES else i
        floor = f // bp + 1
        m = i % min(n, 20) if table in {"TIP_DETAIL", "LIBRARY_RESERVE_MATRL_DETAIL"} else i
        status = i % min(n, 3) if table in {"TIP_DETAIL", "LIBRARY_RESERVE_MATRL_DETAIL"} else i
        use = i % min(n, 5) if table in ROOM_TABLES else i
        term = _term(t)
        first, last = self.people[p]
        full = f"{first} {last}"
        dept_name = f"{DEPARTMENTS[d % 10]} {d // 10 + 1}"
        building = _token("B", b)
        room = str(floor * 100 + i // fp + 1)
        email = f"person{p + 1:04d}@example.com"
        login = f"person{p + 1:04d}"
        subject = f"{i % dp + 1}.{i + 1:03d}"
        title = f"{SUBJECTS[i % 10]} {i // 10 + 1}"
        rng = _random(self.seed, "entity", "metrics", i)
        enrolled = rng.randint(10, 180)
        isbn_body = f"978{m + 1:09d}"
        isbn = isbn_body + str((-sum(int(v) * (1 if j % 2 == 0 else 3)
                                                  for j, v in enumerate(isbn_body))) % 10)
        values = {}

        def put(value, *names):
            for name in names:
                values[name] = value

        put(_token("D", d), "DEPARTMENT_CODE", "DEPARTMENT_NUMBER", "DEPARTMENT", "DEPT_CODE",
            "OFFER_DEPT_CODE", "HR_DEPARTMENT_CODE", "HR_DEPARTMENT_CODE_OLD", "D_CODE", "DLC_CODE")
        put(dept_name, "DEPARTMENT_NAME", "DEPARTMENT_FULL_NAME", "OFFER_DEPT_NAME", "D_NAME",
            "HR_DEPARTMENT_NAME", "HR_DEPARTMENT_NAME_LONG", "HR_DEPARTMENT_NAME_ALPHA",
            "DEPT_NAMES", "DEPT_NAME_IN_COMMENCEMENT_BK")
        put(_token("S", d % 5), "SCHOOL_CODE", "OFFER_SCHOOL_CODE")
        put(f"School {d % 5 + 1}", "SCHOOL_NAME", "OFFER_SCHOOL_NAME", "SCHOOL_NAME_IN_COMMENCEMENT_BK")
        put(_token("DLC", d), "DLC_KEY")
        put(dept_name, "DLC_NAME", "HR_ORG_UNIT_TITLE", "ADMIN_ORG_UNIT_TITLE", "DIRECTORY_ORG_UNIT_TITLE",
            "ORGANIZATION_NAME", "ORGANIZATION", "PERSON_ORGANIZATION", "UNIT", "UNIT_NAME", "SPACE_UNIT")
        put(_token("ORG", d), "FCLT_ORGANIZATION_KEY", "ORGANIZATION_KEY")
        put(_token("OU", d), "HR_ORG_UNIT_ID", "ORGANIZATION_ID", "UNIT_ID", "UNIT_CODE", "SPACE_UNIT_CODE")
        put(_token("ORG", 0), "FCLT_ORG_PARENT_KEY", "ORG_PARENT_KEY", "FCLT_MAJOR_ORG_KEY", "MAJOR_ORG_KEY")
        put(f"{DEPARTMENTS[0]} 1", "ORG_PARENT", "MAJOR_ORG")
        put(d + 1, "DEPT_ID", "PARENT_ID", "ORGANIZATION_SORT", "ORGANIZATION_NUMBER")
        put(_token("D", 0), "PARENT_D_CODE")
        put(f"{DEPARTMENTS[0]} 1", "PARENT_D_NAME")

        put(str(900000001 + p), "MIT_ID", "RESPONSIBLE_FACULTY_MIT_ID", "MOIRA_LIST_MEMBER_MIT_ID")
        put(first, "FIRST_NAME", "PREFERRED_FIRST_NAME", "NAME_KNOWN_BY")
        put(last, "LAST_NAME", "PREFERRED_LAST_NAME")
        put(None, "MIDDLE_NAME", "PREFERRED_MIDDLE_NAME")
        put(full, "FULL_NAME", "DIRECTORY_FULL_NAME", "PERSON_NAME", "RESPONSIBLE_FACULTY_NAME",
            "INSTRUCTOR_NAME", "FALL_INSTRUCTORS", "SPRING_INSTRUCTORS", "MOIRA_LIST_MEMBER_FULL_NAME")
        put(full.upper(), "FULL_NAME_UPPERCASE")
        put(first.upper(), "PREFERRED_FIRST_NAME_UPPER")
        put(last.upper(), "PREFERRED_LAST_NAME_UPPER")
        put(email, "EMAIL_ADDRESS", "PERSON_EMAIL", "MOIRA_LIST_MEMBER")
        put(email.upper(), "EMAIL_ADDRESS_UPPERCASE")
        put(login, "KRB_NAME", "USER_NAME", "USERNAME", "OWNER")
        put(login.upper(), "KRB_NAME_UPPERCASE")
        put(f"+1-617-555-{p % 100:04d}", "OFFICE_PHONE")
        put(f"https://example.com/people/{login}", "PERSONAL_URL")
        put("Professor", "JOB_TITLE", "POSITION_TITLE", "ADMIN_POSITION_TITLE", "ADMIN_JOB_TITLE",
            "DIRECTORY_TITLE", "PRIMARY_TITLE", "PERSON_TITLE", "TITLE")
        put(100, "EMPLOYMENT_PERCENT")
        put(str(p % 4 + 1), "STUDENT_YEAR", "YEAR")
        put("N", "EMERITUS_STATUS", "IS_SABBATICAL", "IS_NO_COURSE_MATERIAL")
        put("Y", "IS_FACULTY", "IS_ACTIVE", "IS_PAID_APPT", "IS_OPA_REQUIRED", "IS_CONSULT_PRIV", "ASSIGNABLE")

        put(_token("TERM", t), "ACADEMIC_TERMS_KEY")
        put(term["code"], "TERM_CODE", "EFFECTIVE_TERM_CODE", "ACADEMIC_TERM_CODE", "SO_TERM_CODE", "FROM_TERM")
        put(_term(t + 1)["code"], "THRU_TERM")
        put(term["title"], "TERM_DESCRIPTION", "ACADEMIC_TERM_DESCRIPTION", "SO_TERM_DESCRIPTION", "FROM_TERM_DESCRIPTION")
        put(_term(t + 1)["title"], "THRU_TERM_DESCRIPTION")
        put(term["year"], "ACADEMIC_YEAR", "ACADEMIC_YEAR_DESC", "FINANCIAL_AID_YEAR", "FINANCIAL_AID_YEAR_DESC", "DEGREE_YEAR")
        put("Y" if t == 0 else "N", "IS_CURRENT_TERM")
        put("Y" if t % 4 in (1, 3) else "N", "IS_REGULAR_TERM")
        put(term["start"], "TERM_START_DATE", "REGISTRATION_DAY", "FIRST_DAY_OF_CLASSES", "GRADUATE_AWARD_START_DATE")
        put(term["end"], "TERM_END_DATE", "LAST_DAY_OF_CLASSES", "LAST_DAY_OF_FINAL_EXAM", "GRADUATE_AWARD_END_DATE")
        put(term["start"] - timedelta(days=30), "PRE_REGISTRATION_START_DAY")
        put(_term(t + 1)["start"] - timedelta(days=1), "TERM_LAST_DAY_BEFORE_NEXT_TERM")
        put("OPEN" if t == 0 else "PLANNED", "TERM_STATUS", "TERM_STATUS_INDICATOR")
        put(subject, "SUBJECT_ID", "MASTER_SUBJECT_ID", "ULT_MASTER_SUBJECT_ID", "SOURCE_SUBJECT_ID",
            "PRINT_SUBJECT_ID", "SO_SUBJECT_ID", "SUBJECT_ID_SORT", "MASTER_SUBJECT_ID_SORT")
        put(_token("SUBJ", i), "SUBJECT_SUMMARY_KEY")
        put(_token("OFFER", i), "SUBJECT_OFFERED_SUMMARY_KEY", "COMPOSITE_SUBJECT_KEY")
        put(_token("GROUP", i), "SUBJECT_GROUPING_KEY", "SUBJECT_GROUP_ID")
        put(_token("D", i % dp), "COURSE_NUMBER", "MASTER_COURSE_NUMBER", "SUBJECT_CODE", "COURSE")
        put(str(i + 1).zfill(3), "SUBJECT_NUMBER")
        put(title, "SUBJECT_TITLE", "SUBJECT_TITLE_LONG", "SUBJECT_SHORT_TITLE", "COURSE_NAME")
        put(f"A synthetic course covering {title.lower()}.", "SUBJECT_DESCRIPTION")
        put("S", "SUBJECT_OR_CLUSTER")
        put(3, "LECTURE_UNITS")
        put(3, "LAB_UNITS")
        put(6, "PREPARATION_UNITS", "PREP_UNITS")
        put(12, "TOTAL_UNITS")
        put(0, "DESIGN_UNITS")
        put(enrolled, "SUBJECT_ENROLLMENT_NUMBER", "CLUSTER_ENROLLMENT_NUMBER", "NUM_ENROLLED_STUDENTS", "SECTION_ENROLLMENT_NUMBER")
        put("1", "SECTION_ID")
        put("Y", "IS_MASTER_SECTION", "IS_LECTURE_SECTION", "IS_OFFERED_THIS_YEAR")
        put("N", "IS_LAB_SECTION", "IS_RECITATION_SECTION", "IS_DESIGN_SECTION")
        put("MWF 10:00-11:00", "MEET_TIME")
        put("10:00", "MEET_START_TIME", "SESSION_START_TIME")
        put("11:00", "MEET_END_TIME", "SESSION_END_TIME")

        put(_token("BLDG", b), "FCLT_BUILDING_KEY", "FAC_BUILDING_KEY", "BUILDING_KEY")
        put(building, "BUILDING_NUMBER")
        put(_token("B", 0), "PARENT_BUILDING_NUMBER")
        put(f"Research Building {b + 1}", "BUILDING_NAME", "BUILDING_NAME_LONG")
        put("Research Building 1", "PARENT_BUILDING_NAME", "PARENT_BUILDING_NAME_LONG")
        put(_token("FLR", f), "FCLT_FLOOR_KEY", "FLOOR_KEY")
        put(str(floor), "FLOOR", "FLOOR_NAME", "FLOOR_SORT_SEQUENCE")
        put(_token("ROOM", i), "FCLT_ROOM_KEY", "FAC_ROOM_KEY", "SPACE_ID")
        put(room, "ROOM", "ROOM_NUMBER")
        put(f"{building}-{room}", "BUILDING_ROOM", "OFFICE_LOCATION", "MEET_PLACE", "SESSION_LOCATION", "PERSON_LOCATION")
        put(f"Room {building}-{room}", "ROOM_FULL_NAME", "BUILDING_ROOM_NAME")
        put(Decimal(200 + i * 5), "AREA", "ROOM_SQUARE_FOOTAGE", "SQFT")
        put(Decimal(10000 + b * 100), "EXT_GROSS_AREA", "BLDG_GROSS_SQUARE_FOOTAGE")
        put(Decimal(7000 + b * 70), "ASSIGNABLE_AREA", "BLDG_ASSIGNABLE_SQUARE_FOOTAGE")
        put(Decimal(3000 + b * 30), "NON_ASSIGNABLE_AREA")
        put(Decimal("42.3600") + Decimal(b) / 10000, "LATITUDE_WGS")
        put(Decimal("-71.0900") - Decimal(b) / 10000, "LONGITUDE_WGS")
        put(_token("USE", use), "FCLT_MAJOR_USE_KEY", "MAJOR_USE_KEY")
        put(("Office", "Laboratory", "Classroom", "Library", "Storage")[use % 5], "MAJOR_USE", "MAJOR_USE_DESC")
        put("Cambridge", "CITY", "CITY_NAME")
        put("MA", "STATE", "STATE_ABBR")
        put("Massachusetts", "STATE_NAME")
        put("02139", "POSTAL_CODE", "ZIP_CODE", "ZIP")
        put("Main Street", "STREET_NAME")
        put(str(100 + b), "STREET_NUMBER")
        put(f"{100 + b} Main Street, Cambridge, MA 02139", "BUILDING_STREET_ADDRESS", "BUILDING_MAILING_ADDRESS", "STREET_ADDRESS")
        put("USA", "COUNTRY_CODE")
        put("United States", "COUNTRY")
        put("N", "PRE_DIRECTIONAL", "POST_DIRECTIONAL")
        put("ST", "STREET_SUFFIX")
        put("MAIN", "SITE")
        put("Academic", "BUILDING_TYPE", "BUILDING_USE")
        put("Owned", "OWNERSHIP_TYPE")
        put("Public", "ACCESS_LEVEL", "ACCESS_LEVEL_NAME")
        put(1, "ACCESS_LEVEL_CODE")
        put("2000-01-01", "DATE_BUILT")
        put("2000-02-01", "DATE_ACQUIRED")
        put("2000-03-01", "DATE_OCCUPIED")

        put(_token("MAT", m), "TIP_MATERIAL_KEY")
        put(_token("CAT", m), "LIBRARY_RESERVE_CATALOG_KEY")
        put(_token("TIP", i), "TIP_SUBJECT_OFFERED_KEY")
        put(_token("LIB", i), "LIBRARY_SUBJECT_OFFERED_KEY")
        put(_token("INST", i if table == "LIBRARY_COURSE_INSTRUCTOR" else i % pp), "LIBRARY_COURSE_INSTRUCTOR_KEY")
        put(_token("STATUS", status), "TIP_MATERIAL_STATUS_KEY", "LIBRARY_MATERIAL_STATUS_KEY")
        put(("Available", "On order", "Reserved")[status % 3], "TIP_MATERIAL_STATUS", "LIBRARY_MATERIAL_STATUS")
        put(isbn, "ISBN", "CATALOG_ISBN")
        put(f"Foundations of {SUBJECTS[m % 10]} {m + 1}", "CATALOG_TITLE")
        put(f"{self.people[m][0]} {self.people[m][1]}", "AUTHOR", "CATALOG_AUTHOR_NAME")
        put("Synthetic Academic Press", "PUBLISHER", "CATALOG_PUBLISHER")
        put("2025", "CATALOG_YEAR")
        price = Decimal(4000 + m * 125) / 100
        put(price, "NEW_SHELF_PRICE")
        put(price * Decimal("0.65"), "USED_SHELF_PRICE")
        put(price * Decimal("0.35"), "RENTAL_NEW_PRICE")
        put(price * Decimal("0.25"), "RENTAL_USED_PRICE")
        if table == "TIP_MATERIAL":
            put(values["CATALOG_TITLE"], "TITLE")
            put("2025", "YEAR")
        if table == "ZIP_CANADA":
            put("Toronto", "CITY_NAME")
            put("ON", "PROVINCE_ABBR")
            put("Ontario", "PROVINCE_NAME")
            put(f"M5V {i % 10}A{i % 10}", "POSTAL_CODE")
        for key in ("IAP_SUBJECT_CATEGORY_KEY", "IAP_SUBJECT_SPONSOR_KEY", "IAP_SUBJECT_SESSION_KEY", "IAP_SUBJECT_PERSON_KEY"):
            put(_token(key, i % min(n, 10) if table == "IAP_SUBJECT_DETAIL" else i), key)
        put(_token("LIST", i % min(n, 10) if table == "MOIRA_LIST_DETAIL" else i), "MOIRA_LIST_KEY")
        put(_token("OWNER", i % pp if table == "MOIRA_LIST_DETAIL" else i), "MOIRA_LIST_OWNER_KEY")
        put(_token("SPACE", d if table in {"SPACE_UNIT", "SPACE_UNIT2"} else i % dp), "SPACE_UNIT_KEY")
        put(i + 1 if table == "SPACE_USAGE" else i % min(n, 5) + 1, "SPACE_USAGE_KEY")
        put("A", "RECORD_TYPE")
        put(1, "COUNTER", "RECORD_COUNT", "RECORD_COUNTER", "ROOM_COUNTER", "BUILDING_COUNTER")
        put(SNAPSHOT, "WAREHOUSE_LOAD_DATE")
        put(SNAPSHOT - timedelta(days=30), "DATE_FROM", "START_DATE", "VALID_FROM_DATE", "SABBATICAL_BEGIN_DATE", "CATALOG_RECORD_CREATE_DATE", "CREATE_DATE")
        put(SNAPSHOT + timedelta(days=60), "DATE_TO", "END_DATE", "VALID_THRU_DATE", "SABBATICAL_END_DATE")
        put(SNAPSHOT - timedelta(days=1), "LAST_ACTIVITY_DATE", "LAST_UPDATE_DATE", "CATALOG_RECORD_UPDATE_DATE")
        for name in ("TIME_MONTH_KEY", "FISCAL_PERIOD", "CALENDAR_PERIOD"):
            put(_token("MONTH", i) if name == "TIME_MONTH_KEY" else f"{2026 + i // 12}{i % 12 + 1:02d}", name)
        put(str(2026 + i // 12), "FISCAL_YEAR", "CALENDAR_YEAR")
        return values

    def _value(self, table: str, column: Column, i: int, values: dict):
        name = column.name
        rng = _random(self.seed, table, name, i)
        if name in values:
            value = values[name]
        elif name.startswith(("IS_", "HAS_")) or name in {"ASSIGNABLE", "EMERITUS_STATUS"}:
            value = "Y" if i % 3 else "N"
        elif name.endswith(("_KEY", "_ID", "_CODE")):
            value = _token(name, i)
        elif column.source_type == "NUMBER":
            value = Decimal(rng.randint(100, 100000)) / 100 if any(
                token in name for token in ("AMOUNT", "BUDGET", "PRICE", "FEE", "VOLUME", "AREA", "SQFT")
            ) else rng.randint(1, 100)
        elif column.source_type == "DATE":
            value = SNAPSHOT + timedelta(days=rng.randint(-365, 0))
        else:
            value = f"{name.replace('_', ' ').title()} {i + 1:03d}"
        if value is None:
            return None
        if column.source_type == "VARCHAR2":
            return value.isoformat(sep=" ") if isinstance(value, datetime) else str(value)
        if column.source_type == "CHAR":
            return str(value)[0]
        if column.source_type == "NUMBER":
            try:
                return Decimal(str(value))
            except InvalidOperation:
                # Source mistakes such as FAC_BUILDING.BUILDING_NAME NUMBER
                # still receive valid numbers, never a silent schema correction.
                return Decimal(i + 1)
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            # E.g. CIS_COURSE_CATALOG.DEPARTMENT_NAME DATE.
            return SNAPSHOT + timedelta(days=i)

    def table_rows(self, table: Table) -> list[tuple]:
        result = []
        for i in range(self.rows):
            values = self._values(table.name, i)
            result.append(tuple(self._value(table.name, c, i, values) for c in table.columns))
        return result

    def generate(self, tables: tuple[Table, ...]) -> dict[str, list[tuple]]:
        return {table.name: self.table_rows(table) for table in tables}
