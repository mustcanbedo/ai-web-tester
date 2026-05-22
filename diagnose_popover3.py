"""精准诊断：Popover 打开后存活时间 + CDP AX Tree 是否能看到菜单项"""
import json, time
from playwright.sync_api import sync_playwright

TARGET = "https://cloudtest.manifoldtech.cn"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()

    # 恢复登录态
    with open("session_cookies.json") as f:
        data = json.load(f)
        ctx.add_cookies(data.get("context_cookies", []))

    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)

    page.goto(TARGET)
    page.wait_for_load_state("networkidle")
    ls = data.get("localStorage", {}).get("result", "{}")
    if ls and ls != "{}":
        page.evaluate("() => { try { var d = %s; for(var k in d) localStorage.setItem(k,d[k]); } catch(e){} }" % ls)
    page.reload()
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # 进入 Mesh 任务列表
    page.goto(TARGET + "/training/list?projectId=39&tab=mesh")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    print("[INFO] URL:", page.url)

    # 找到 ··· 按钮
    btn = page.query_selector('button[aria-haspopup="dialog"]')
    if not btn:
        print("[FATAL] No haspopup button")
        browser.close()
        exit(1)

    # ====== 测试 1: Popover 存活时间 ======
    print("\n=== TEST 1: Popover 存活时间 ===")
    btn.click(timeout=3000)
    for delay in [0.3, 0.5, 1.0, 1.5, 2.0]:
        time.sleep(0.3 if delay == 0.3 else delay - ([0, 0.3, 0.5, 1.0, 1.5][int(delay*2-1)] if False else 0))
        # 每隔 0.3s 检查
        pass

    # 简单做法：click 后连续检查
    page.keyboard.press("Escape")
    time.sleep(0.5)

    btn.click(timeout=3000)
    checks = []
    start = time.time()
    for _ in range(20):
        elapsed = time.time() - start
        state = btn.get_attribute("data-state")
        dlg = page.query_selector('[role="dialog"]')
        dlg_vis = dlg.is_visible() if dlg else False
        dlg_text = dlg.text_content().strip()[:60] if dlg and dlg_vis else ""
        checks.append(f"  t={elapsed:.1f}s state={state} dialog={dlg_vis} text='{dlg_text}'")
        if elapsed > 3.0:
            break
        time.sleep(0.2)

    for c in checks:
        print(c)

    # 关闭 popover
    page.keyboard.press("Escape")
    time.sleep(0.5)

    # ====== 测试 2: CDP AX Tree 捕获 ======
    print("\n=== TEST 2: CDP AX Tree 在 Popover 打开时 ===")
    btn.click(timeout=3000)
    time.sleep(0.5)

    state = btn.get_attribute("data-state")
    print(f"data-state: {state}")

    # 用 CDP 获取 AX Tree
    result = cdp.send("Accessibility.getFullAXTree")
    ax_nodes = result.get("nodes", [])

    # 找 dialog 相关节点
    dialog_nodes = []
    menuitem_nodes = []
    for n in ax_nodes:
        role = n.get("role", {}).get("value", "")
        name = n.get("name", {}).get("value", "")
        ignored = n.get("ignored", False)
        if ignored:
            continue
        if role == "dialog":
            dialog_nodes.append({"role": role, "name": name})
        if role == "menuitem":
            menuitem_nodes.append({"role": role, "name": name})
        # 也找 button with name 重命名/删除
        if role == "button" and name in ("重命名", "删除"):
            menuitem_nodes.append({"role": role, "name": name})

    print(f"AX Tree total: {len(ax_nodes)} nodes")
    print(f"dialog nodes: {dialog_nodes}")
    print(f"menuitem/relevant buttons: {menuitem_nodes}")

    # 找所有非 ignored 节点中 name 含 "重命名" 或 "删除" 的
    matching = []
    for n in ax_nodes:
        if n.get("ignored"):
            continue
        name = n.get("name", {}).get("value", "")
        role = n.get("role", {}).get("value", "")
        if "重命名" in name or "删除" in name:
            matching.append({"role": role, "name": name})
    print(f"含 '重命名'/'删除' 的 AX 节点: {matching}")

    # ====== 测试 3: snapshot 通过我们的 bridge 获取 ======
    print("\n=== TEST 3: 通过 PlaywrightBridge.snapshot() ===")
    # 保持 popover 打开，导入 bridge
    import sys
    sys.path.insert(0, ".")
    from playwright_bridge import PlaywrightBridge

    # 创建一个 bridge 实例并复用 page/cdp
    bridge = PlaywrightBridge.__new__(PlaywrightBridge)
    bridge._page = page
    bridge._cdp = cdp
    bridge._pages = [page]
    bridge._ref_map = {}
    bridge._ref_counter = 0
    bridge.INTERACTIVE_ROLES = {
        "RootWebArea", "link", "button", "textbox", "combobox", "searchbox",
        "radio", "checkbox", "switch", "slider", "spinbutton", "tab",
        "menuitem", "menuitemcheckbox", "menuitemradio", "option",
        "treeitem", "listbox", "dialog", "alertdialog",
    }

    snap = bridge.snapshot(filter_interactive=True)
    refs = snap.get("nodes", [])

    # 找 dialog / 重命名 / 删除
    dialog_refs = [r for r in refs if r["role"] == "dialog"]
    rename_refs = [r for r in refs if "重命名" in r.get("name", "") or "删除" in r.get("name", "")]
    print(f"snapshot refs total: {len(refs)}")
    print(f"dialog refs: {dialog_refs}")
    print(f"重命名/删除 refs: {rename_refs}")

    # 列出所有 refs 看看有没有 popover 菜单
    print("\n所有 refs:")
    for r in refs:
        print(f"  {r['ref']} role={r['role']} name='{r.get('name','')[:40]}'")

    browser.close()
    print("\n[DONE]")
