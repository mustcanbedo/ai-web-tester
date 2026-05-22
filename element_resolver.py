"""
AI Web Tester - 智能元素定位策略模块
根据结构化描述（strategy）在 AX Tree refs 中精确定位目标元素，
取代 LLM 猜 ref 的不确定性。
"""


def resolve_element(refs, strategy, page_text=""):
    """
    根据策略在 refs 列表中定位目标元素，返回 ref ID。

    strategy 格式:
      {"strategy": "by_text", "text": "重命名", "role": "menuitem"}
      {"strategy": "by_role", "role": "textbox", "fallback_name_contains": "搜索"}
      {"strategy": "card_menu_button", "exclude_text": "projectId=25"}

    Returns:
      (ref_id, match_info) 或 (None, error_msg)
    """
    strategy_type = strategy.get("strategy", "")

    if strategy_type == "by_text":
        return _resolve_by_text(refs, strategy)
    elif strategy_type == "by_role":
        return _resolve_by_role(refs, strategy)
    elif strategy_type == "card_menu_button":
        return _resolve_card_menu_button(refs, strategy)
    elif strategy_type == "by_name":
        return _resolve_by_name(refs, strategy)
    else:
        return None, f"未知的定位策略: {strategy_type}"


def _resolve_by_text(refs, strategy):
    """按文本内容匹配元素"""
    target_text = strategy.get("text", "")
    target_role = strategy.get("role")
    exact = strategy.get("exact", False)
    index = strategy.get("index", 0)

    if not target_text:
        return None, "by_text 策略需要 text 参数"

    candidates = []
    for r in refs:
        name = r.get("name", "")
        role = r.get("role", "")
        if r.get("disabled"):
            continue
        if target_role and role != target_role:
            continue
        if exact:
            if name == target_text:
                candidates.append(r)
        else:
            if target_text in name or name in target_text:
                candidates.append(r)

    if not candidates:
        # 放宽：不限 role 再试一次
        if target_role:
            relaxed = dict(strategy)
            relaxed.pop("role", None)
            return _resolve_by_text(refs, relaxed)
        return None, f"未找到包含文本 '{target_text}' 的元素"

    if index < len(candidates):
        hit = candidates[index]
        return hit["ref"], f"by_text 匹配: '{hit.get('name', '')}' (role={hit.get('role', '')})"
    else:
        hit = candidates[0]
        return hit["ref"], f"by_text 匹配(index={index}超出，取首个): '{hit.get('name', '')}'"


def _resolve_by_role(refs, strategy):
    """按角色匹配元素"""
    target_role = strategy.get("role", "")
    fallback_role = strategy.get("fallback_role")
    name_contains = strategy.get("name_contains")
    fallback_name_contains = strategy.get("fallback_name_contains")
    index = strategy.get("index", 0)
    exclude_text = strategy.get("exclude_text", "")

    if not target_role:
        return None, "by_role 策略需要 role 参数"

    candidates = _find_by_role(refs, target_role, name_contains, exclude_text)

    # 主策略未命中，尝试 fallback
    if not candidates and fallback_role:
        candidates = _find_by_role(refs, fallback_role, fallback_name_contains or name_contains, exclude_text)

    if not candidates:
        return None, f"未找到 role={target_role} 的元素"

    if index < len(candidates):
        hit = candidates[index]
        return hit["ref"], f"by_role 匹配: role={hit.get('role', '')} name='{hit.get('name', '')[:40]}'"
    else:
        hit = candidates[0]
        return hit["ref"], f"by_role 匹配(index 超出，取首个): role={hit.get('role', '')} name='{hit.get('name', '')[:40]}'"


def _find_by_role(refs, role, name_contains=None, exclude_text=""):
    """辅助：按 role 筛选 refs"""
    results = []
    for r in refs:
        if r.get("role", "") != role:
            continue
        if r.get("disabled"):
            continue
        name = r.get("name", "")
        if exclude_text and exclude_text in name:
            continue
        if name_contains and name_contains not in name:
            continue
        results.append(r)
    return results


def _resolve_card_menu_button(refs, strategy):
    """
    定位卡片菜单按钮（···）。
    策略：找 name 含 '[更多操作]' 或 '[唯一按钮 @' 的 button，
    排除 exclude_text 指定的项目。
    如果有 name_contains，优先匹配。
    """
    exclude_text = strategy.get("exclude_text", "")
    name_contains = strategy.get("name_contains", "")

    # 候选：所有可能是菜单按钮的元素
    candidates = []
    for r in refs:
        role = r.get("role", "")
        name = r.get("name", "")
        if role != "button":
            continue
        if r.get("disabled"):
            continue
        if exclude_text and exclude_text in name:
            continue
        # 识别菜单按钮的特征
        is_menu_btn = False
        if "[更多操作]" in name:
            is_menu_btn = True
        elif "[唯一按钮 @" in name or "[唯一按钮]" in name:
            is_menu_btn = True
        elif name.startswith("[按钮") and "/" in name:
            # [按钮1/2 @ xxx] 格式：取编号最大的（最后一个通常是菜单）
            is_menu_btn = True
        if is_menu_btn:
            candidates.append(r)

    if not candidates:
        return None, "未找到卡片菜单按钮（[更多操作] / [唯一按钮 @]）"

    # 优先匹配 name_contains
    if name_contains:
        preferred = [c for c in candidates if name_contains in c.get("name", "")]
        if preferred:
            candidates = preferred

    # 优先选 [更多操作] 类型
    more_btns = [c for c in candidates if "[更多操作]" in c.get("name", "")]
    if more_btns:
        hit = more_btns[0]
        return hit["ref"], f"card_menu_button 匹配: '{hit.get('name', '')[:50]}'"

    # 其次选 [唯一按钮]
    solo_btns = [c for c in candidates if "[唯一按钮" in c.get("name", "")]
    if solo_btns:
        hit = solo_btns[0]
        return hit["ref"], f"card_menu_button 匹配: '{hit.get('name', '')[:50]}'"

    # 最后取第一个
    hit = candidates[0]
    return hit["ref"], f"card_menu_button 匹配(兜底): '{hit.get('name', '')[:50]}'"


def _resolve_by_name(refs, strategy):
    """按 name 精确或模糊匹配"""
    target_name = strategy.get("name", "")
    target_role = strategy.get("role")
    exact = strategy.get("exact", False)

    if not target_name:
        return None, "by_name 策略需要 name 参数"

    for r in refs:
        if target_role and r.get("role", "") != target_role:
            continue
        name = r.get("name", "")
        if exact and name == target_name:
            return r["ref"], f"by_name 精确匹配: '{name}'"
        elif not exact and target_name in name:
            return r["ref"], f"by_name 模糊匹配: '{name[:50]}'"

    return None, f"未找到 name 包含 '{target_name}' 的元素"
