"""诊断 Popover "更多"按钮点击失败问题
目标：找到任务卡片上的 ··· 按钮，检查其属性，测试各种 click 方式
"""
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
                print(f"[OK] 恢复 {len(cookies)} 个 cookies")
    except Exception as e:
        print(f"[WARN] cookies 恢复失败: {e}")

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
                page.evaluate(f"""() => {{
                    try {{
                        var d = JSON.parse('{ls_data.replace(chr(92), chr(92)+chr(92)).replace(chr(39), chr(92)+chr(39))}');
                        for (var k in d) localStorage.setItem(k, d[k]);
                    }} catch(e) {{ console.error('ls restore fail', e); }}
                }}""")
                print("[OK] localStorage 已恢复")
        page.reload()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
    except:
        pass

    # 检查是否已登录
    login_btn = page.query_selector('text=登录')
    if login_btn and login_btn.is_visible():
        print("[FATAL] 未登录，无法继续")
        browser.close()
        exit(1)
    print("[OK] 已登录")

    # 进入一个有任务的项目
    # 先去项目列表，找第一个项目点进去
    page.goto(f"{TARGET_URL}/datasets/list")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # 找到第一个项目卡片，点击进入
    clickable_cards = page.query_selector_all('[class*="cursor-pointer"]')
    print(f"[INFO] 找到 {len(clickable_cards)} 个可点击卡片")

    if not clickable_cards:
        # 尝试用 clickable 元素
        clickable_cards = page.query_selector_all('div[data-clickable]')
        print(f"[INFO] 找到 {len(clickable_cards)} 个 data-clickable 元素")

    # 用链接进入第一个项目
    project_links = page.query_selector_all('a[href*="/training/list"]')
    if project_links:
        print(f"[INFO] 找到 {len(project_links)} 个项目链接，点击第一个")
        project_links[0].click()
    else:
        # 直接导航到已知项目
        print("[INFO] 未找到项目链接，直接导航到 projectId=39")
        page.goto(f"{TARGET_URL}/training/list?projectId=39&tab=mesh")

    page.wait_for_load_state("networkidle")
    time.sleep(2)

    print(f"\n[INFO] 当前 URL: {page.url}")
    print(f"[INFO] 页面标题: {page.title()}")

    # ========== 核心诊断：找到所有 button，分析 aria-haspopup ==========
    print("\n" + "="*60)
    print("诊断 1：扫描所有 button 的 aria-haspopup 属性")
    print("="*60)

    buttons_info = page.evaluate("""() => {
        const buttons = document.querySelectorAll('button');
        return Array.from(buttons).map((btn, i) => ({
            index: i,
            text: btn.textContent.trim().substring(0, 50),
            ariaHaspopup: btn.getAttribute('aria-haspopup'),
            dataState: btn.getAttribute('data-state'),
            ariaExpanded: btn.getAttribute('aria-expanded'),
            role: btn.getAttribute('role'),
            tagName: btn.tagName,
            className: btn.className.substring(0, 80),
            isVisible: btn.offsetParent !== null,
            rect: btn.getBoundingClientRect().toJSON(),
            // 检查祖先
            parentHaspopup: btn.parentElement?.getAttribute('aria-haspopup'),
            grandparentHaspopup: btn.parentElement?.parentElement?.getAttribute('aria-haspopup'),
        }));
    }""")

    popup_buttons = [b for b in buttons_info if b.get('ariaHaspopup') or b.get('parentHaspopup') or b.get('grandparentHaspopup')]
    print(f"\n找到 {len(popup_buttons)} 个有 aria-haspopup 的按钮：")
    for b in popup_buttons:
        print(f"  [{b['index']}] text='{b['text']}' haspopup={b['ariaHaspopup']} parent={b['parentHaspopup']} "
              f"state={b['dataState']} expanded={b['ariaExpanded']} visible={b['isVisible']}")

    # 找 ··· 按钮（通常文本为空或只有图标）
    print(f"\n所有可见的空文本/图标按钮：")
    icon_buttons = [b for b in buttons_info if b.get('isVisible') and len(b.get('text', '')) <= 3]
    for b in icon_buttons:
        print(f"  [{b['index']}] text='{b['text']}' haspopup={b['ariaHaspopup']} state={b['dataState']} "
              f"class={b['className'][:60]} rect=w{b['rect']['width']:.0f}h{b['rect']['height']:.0f}")

    # ========== 诊断 2：找到 ··· 按钮并测试点击 ==========
    print("\n" + "="*60)
    print("诊断 2：测试 ··· 按钮点击效果")
    print("="*60)

    # 找所有可能的 ··· 按钮（aria-haspopup 或 小图标按钮）
    target_buttons = page.query_selector_all('button[aria-haspopup]')
    print(f"\n找到 {len(target_buttons)} 个 button[aria-haspopup] 元素")

    if not target_buttons:
        # 回退：找所有小按钮
        target_buttons = page.query_selector_all('button')
        target_buttons = [b for b in target_buttons if b.is_visible() and len(b.text_content().strip()) <= 3]
        print(f"回退：找到 {len(target_buttons)} 个小图标按钮")

    for i, btn in enumerate(target_buttons[:3]):  # 只测前3个
        text = btn.text_content().strip()
        haspopup = btn.get_attribute('aria-haspopup')
        data_state_before = btn.get_attribute('data-state')
        print(f"\n--- 测试按钮 {i}: text='{text}' haspopup={haspopup} state_before={data_state_before} ---")

        # 方式1: locator.click()
        try:
            btn.click(timeout=3000)
            time.sleep(0.5)
            data_state_after = btn.get_attribute('data-state')
            # 检查是否有新弹出的 popover content
            popover = page.query_selector('[data-radix-popper-content-wrapper]')
            popover_visible = popover and popover.is_visible() if popover else False
            print(f"  locator.click(): state_after={data_state_after} popover_visible={popover_visible}")

            # 如果打开了，关闭它
            if popover_visible or data_state_after == 'open':
                page.keyboard.press("Escape")
                time.sleep(0.3)
        except Exception as e:
            print(f"  locator.click() 失败: {e}")

        time.sleep(0.5)

        # 方式2: el.click() (JS)
        try:
            btn.evaluate("el => el.click()")
            time.sleep(0.5)
            data_state_after2 = btn.get_attribute('data-state')
            popover2 = page.query_selector('[data-radix-popper-content-wrapper]')
            popover_visible2 = popover2 and popover2.is_visible() if popover2 else False
            print(f"  el.click(): state_after={data_state_after2} popover_visible={popover_visible2}")

            if popover_visible2 or data_state_after2 == 'open':
                page.keyboard.press("Escape")
                time.sleep(0.3)
        except Exception as e:
            print(f"  el.click() 失败: {e}")

        time.sleep(0.5)

        # 方式3: dispatchEvent
        try:
            btn.evaluate("""el => {
                el.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                el.dispatchEvent(new PointerEvent('pointerup', {bubbles:true}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
            }""")
            time.sleep(0.5)
            data_state_after3 = btn.get_attribute('data-state')
            popover3 = page.query_selector('[data-radix-popper-content-wrapper]')
            popover_visible3 = popover3 and popover3.is_visible() if popover3 else False
            print(f"  dispatchEvent: state_after={data_state_after3} popover_visible={popover_visible3}")

            if popover_visible3 or data_state_after3 == 'open':
                page.keyboard.press("Escape")
                time.sleep(0.3)
        except Exception as e:
            print(f"  dispatchEvent 失败: {e}")

    # ========== 诊断 3：检查 Radix Vue 的 DropdownMenu 结构 ==========
    print("\n" + "="*60)
    print("诊断 3：Radix Vue DropdownMenu 组件结构")
    print("="*60)

    radix_info = page.evaluate("""() => {
        // 查找所有 Radix 相关的 data 属性
        const all = document.querySelectorAll('[data-radix-collection-item], [data-state], [data-side], [data-radix-popper-content-wrapper]');
        return Array.from(all).slice(0, 20).map(el => ({
            tag: el.tagName,
            dataState: el.getAttribute('data-state'),
            dataSide: el.getAttribute('data-side'),
            role: el.getAttribute('role'),
            ariaHaspopup: el.getAttribute('aria-haspopup'),
            text: el.textContent.trim().substring(0, 30),
            visible: el.offsetParent !== null,
        }));
    }""")
    for item in radix_info:
        print(f"  <{item['tag']}> state={item['dataState']} side={item['dataSide']} role={item['role']} "
              f"haspopup={item['ariaHaspopup']} visible={item['visible']} text='{item['text']}'")

    browser.close()
    print("\n[DONE] 诊断完成")
