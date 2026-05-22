# DemoQA 表单交互测试

> 目标网址：https://demoqa.com/text-box
> 无需登录

## 通用规则
- 每步操作后观察页面变化
- 提交表单后检查输出区域是否正确显示

---

### 1. 文本框表单填写
**步骤：**
1. 访问 https://demoqa.com/text-box
2. 在 "Full Name" 输入框输入 "Zhang San"
3. 在 "Email" 输入框输入 "zhangsan@test.com"
4. 在 "Current Address" 文本框输入 "Beijing China"
5. 在 "Permanent Address" 文本框输入 "Shanghai China"
6. 点击 "Submit" 按钮
7. 确认页面下方出现绿色边框的输出区域，显示刚才填写的信息

**结果预期：**
1. 输出区域显示 Name、Email、Current Address、Permanent Address
2. 显示内容与填写内容一致

---

### 2. 复选框交互
**步骤：**
1. 访问 https://demoqa.com/checkbox
2. 点击展开 "Home" 节点左侧的箭头
3. 观察展开的子节点（Desktop、Documents、Downloads）
4. 点击 "Desktop" 的复选框使其选中
5. 确认 "Desktop" 下的子项也被自动选中
6. 确认页面下方显示选中项的文字

**结果预期：**
1. 勾选父级节点时子节点自动全选
2. 页面底部显示所有选中项名称

---

### 3. 单选按钮
**步骤：**
1. 访问 https://demoqa.com/radio-button
2. 点击 "Yes" 单选按钮
3. 确认页面显示 "You have selected Yes"
4. 点击 "Impressive" 单选按钮
5. 确认页面显示变为 "You have selected Impressive"

**结果预期：**
1. 选择单选按钮后页面正确反馈选中状态
2. 切换选项后反馈内容更新
