"""把规范Markdown台账的行与列标题映射为属性关系，所有名称和谓词均来自原表格。"""

import re


def table_claims(content: str) -> dict | None:
    """首列作为行主体，其余命名值作为属性对象，数值和日期保留在原文及时间限定中。"""
    lines = content.splitlines(keepends=True)
    offsets, offset = [], 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    entities, relations, identities = [], [], {}

    def cells(line):
        # 含转义管道符的表格交给模型处理，避免错误分列。
        if "\\|" in line:
            return []
        return [value.strip() for value in line.strip().strip("|").split("|")]

    def entity(name, kind, quote):
        key = (name, kind, quote)
        if key not in identities:
            identities[key] = len(entities)
            entities.append({"name": name, "mention": name, "entity_type": kind, "scope": "", "quote": quote})
        return identities[key]

    def named(value):
        if not value or value in {"-", "—", "无", "不适用"} or len(value) > 128:
            return False
        # 容量、金额与日期是属性值，避免把不同单位的同一个数字错误合并成实体。
        return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", value)) and not re.fullmatch(r"[\d\s.,，%％年月日至/\-~—–万元人台套个]+", value)

    def kind(header):
        if re.search(r"姓名|教师|负责人|联系人", header):
            return "person"
        if re.search(r"部门|单位|学院|机构|企业|院系", header):
            return "organization"
        return "concept"

    for index in range(len(lines) - 2):
        if "|" not in lines[index] or not re.fullmatch(r"[\s|:\-]+", lines[index + 1]):
            continue
        headers = cells(lines[index])
        if len(headers) < 2 or not all(headers):
            continue
        row_index = index + 2
        while row_index < len(lines) and "|" in lines[row_index]:
            values = cells(lines[row_index])
            if len(values) != len(headers) or not named(values[0]):
                row_index += 1
                continue
            row_quote = lines[row_index].strip()
            proof = content[offsets[index]:offsets[row_index] + len(lines[row_index])].strip()
            if len(row_quote) > 1000 or len(proof) > 10000:
                return None
            time = next((value for header, value in zip(headers, values)
                         if re.search(r"期限|起止时间|日期|^年度$|^年份$", header) and re.search(r"\d{4}", value) and len(value) <= 255), "")
            subject = None
            for header, value in zip(headers[1:], values[1:]):
                if not named(value) or value == values[0] or len(header) > 255:
                    continue
                if subject is None:
                    subject = entity(values[0], kind(headers[0]), row_quote)
                target = entity(value, kind(header), row_quote)
                relations.append({"subject": subject, "object": target, "predicate": header,
                    "quote": proof, "qualifiers": {"time": time, "condition": "",
                        "polarity": "negated" if re.search(r"不承担|禁止|不允许", header) else "affirmed"}})
            row_index += 1
    if not relations or len(entities) > 100 or len(relations) > 100:
        return None
    return {"entities": entities, "relations": relations, "events": []}
