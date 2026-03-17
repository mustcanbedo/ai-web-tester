# TodoMVC 应用核心功能预期

## 一、测试入口
- URL：https://todomvc.com/examples/react/dist/

## 二、核心流程

### 1. 新增待办事项
**预期步骤：**
1. 在输入框中输入 "学习 Playwright"
2. 按下回车键

**结果预期：**
- 待办事项列表中出现一个新的条目，内容为 "学习 Playwright"
- 页面左下角的计数器显示 "1 item left"
- 页面不应出现 JS console error

**失败条件：**
- 列表未出现新条目，或内容不符
- 计数器未更新或数字错误
- 出现 console error

---

### 2. 完成待办事项
**前置条件：** 已存在一个内容为 "学习 Playwright" 的待办事项

**预期步骤：**
1. 点击 "学习 Playwright" 条目前面的圆形选择框

**结果预期：**
- "学习 Playwright" 条目被划上删除线，并标记为已完成状态
- 页面左下角的计数器显示 "0 items left"
- 页面底部出现 "Clear completed" 按钮
- 页面不应出现 JS console error

**失败条件：**
- 条目未被标记为完成
- 计数器未更新
- "Clear completed" 按钮未出现
- 出现 console error

---

### 3. 删除待办事项
**前置条件：** 已存在一个内容为 "学习 Playwright" 的待办事项

**预期步骤：**
1. 鼠标悬停在 "学习 Playwright" 条目上
2. 点击出现的 "×" 删除按钮

**结果预期：**
- "学习 Playwright" 条目从列表中消失
- 待办事项列表变为空
- 页面不应出现 JS console error

**失败条件：**
- 条目未被删除
- 出现 console error

## 三、通用规则
- 任意页面和操作过程中，禁止出现致命的 JS console error。
- 页面加载时间不应超过 5 秒。
