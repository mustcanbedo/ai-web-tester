"""诊断 Popover 打开后 AX Tree 是否能看到菜单选项"""
import json, time
from playwright.sync_api import sync_playwright

TARGET_URL = "https://cloudtest.manifoldtech.cn"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()

    # 恢复 cookies
    try:
        with open("session_cookies.json", "r") as f:
            data = json.load(f)
            cookies = data.get("context_cookies", [])
            if cookies:
                context.add_cookies(cookies)
    except:
        pass

    page = context.new_page()
    page.goto(TARGET_URL)
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # 恢复 localStorage
    try:
        with open("session_cookies.json", "r") as f:
            data = json.load(f)
            ls_data = data.get("localStorage", {}).get("result", "{}")
            if ls_data and ls_data != "{}":
                page.evaluate("""() => {
                    try {
                        var d = JSON.parse('%s');
                        for (var k in d) localStorage.setItem(k, d[k]);
                    } catch(e) {}
                }""" % ls_data.replace("\\", "\\\\").replace("'", "\\'"))
        page.reload()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
    except:
        pass

    # 导航到任务列表
    page.goto(TARGET_URL + "/training/list?projectId=39&tab=mesh")
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    print("[INFO] URL:", page.url)

    # 找到 ··· 按钮 (aria-haspopup=dialog)
    btns = page.query_selector_all('button[aria-haspopup="dialog"]')
    print("[INFO] haspopup=dialog buttons:", len(btns))

    if not btns:
        print("[FATAL] No popup buttons found")
        browser.close()
        exit(1)

    btn = btns[0]
    print("\n=== BEFORE CLICK ===")
    print("data-state:", btn.get_attribute("data-state"))

    # 点击按钮
    print("\n=== CLICKING ===")
    btn.click(timeout=3000)
    time.sleep(1)  # 等 Popover 动画

    print("data-state after:", btn.get_attribute("data-state"))

    # 检查 DOM 中的 popover content
    popovers = page.query_selector_all('[data-radix-popper-content-wrapper]')
    print("Popper wrappers:", len(popovers))
    for i, pop in enumerate(popovers):
        visible = pop.is_visible()
        html = pop.inner_html()[:200]
        print(f"  [{i}] visible={visible} html={html}")

    # 检查 role=dialog 元素
    dialogs = page.query_selector_all('[role="dialog"]')
    print("role=dialog elements:", len(dialogs))
    for i, d in enumerate(dialogs):
        visible = d.is_visible()
        text = d.text_content().strip()[:100]
        print(f"  [{i}] visible={visible} text='{text}'")

    # 检查 role=menuitem
    menuitems = page.query_selector_all('[role="menuitem"]')
    print("role=menuitem elements:", len(menuitems))
    for i, m in enumerate(menuitems):
        visible = m.is_visible()
        text = m.text_content().strip()[:50]
        print(f"  [{i}] visible={visible} text='{text}'")

    # 特别检查：Popover 是通过 Teleport 渲染到 body 末尾的，检查 body 最后几个子元素
    print("\n=== BODY LAST CHILDREN ===")
    body_last = page.evaluate("""() => {
        const body = document.body;
        const children = Array.from(body.children);
        return children.slice(-5).map(el => ({
            tag: el.tagName,
            id: el.id,
            className: el.className.substring(0, 60),
            dataState: el.getAttribute('data-state'),
            role: el.getAttribute('role'),
            childCount: el.children.length,
            text: el.textContent.trim().substring(0, 80),
            visible: el.offsetParent !== null || getComputedStyle(el).display !== 'none',
        }));
    }""")
    for item in body_last:
        print(f"  <{item['tag']}> id={item['id']} class={item['className']} "
              f"state={item['dataState']} role={item['role']} children={item['childCount']} "
              f"visible={item['visible']} text='{item['text'][:50]}'")

    # 关闭
    page.keyboard.press("Escape")
    time.sleep(0.3)
    browser.close()
    print("\n[DONE]")
