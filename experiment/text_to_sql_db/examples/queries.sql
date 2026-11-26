-- 기본 DB_SCHEMA=public 기준. 사용자 스키마라면 search_path를 변경하세요.
SET search_path TO public;

-- 학기·학과별 개설 과목 수와 수강 인원 합계
SELECT t.term_description, d.department_name,
       COUNT(*) AS subject_count,
       SUM(s.subject_enrollment_number) AS enrollment_count
FROM subject_summary s
JOIN academic_terms t USING (term_code)
JOIN sis_department d USING (department_code)
GROUP BY t.term_description, d.department_name
ORDER BY t.term_description, d.department_name;

-- 건물·층별 호실 수와 면적 합계
SELECT b.building_name, f.floor, COUNT(*) AS room_count, SUM(r.area) AS total_area
FROM fclt_rooms r
JOIN fclt_floor f USING (fclt_floor_key, fclt_building_key)
JOIN fclt_building b USING (fclt_building_key)
GROUP BY b.building_name, f.floor
ORDER BY b.building_name, f.floor;

-- 직원과 소속 조직: 인명·이메일은 동일 MIT_ID에 맞춰 공유합니다.
SELECT e.mit_id, e.full_name, e.email_address, h.hr_org_unit_title
FROM employee_directory e
JOIN drupal_employee_directory d USING (mit_id)
JOIN hr_org_unit h USING (hr_org_unit_id)
ORDER BY e.mit_id;

-- 개설 과목별 교재와 가격·상태
SELECT s.term_code, s.subject_id, s.subject_title,
       m.isbn, m.title, m.new_shelf_price, st.tip_material_status
FROM tip_detail d
JOIN tip_subject_offered s USING (tip_subject_offered_key, term_code, subject_id)
JOIN tip_material m USING (tip_material_key, isbn)
JOIN tip_material_status st USING (tip_material_status_key)
ORDER BY s.term_code, s.subject_id;
