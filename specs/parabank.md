# ParaBank 网上银行测试

> 目标网址：https://parabank.parasoft.com/parabank/index.htm
> 需要注册新账号

## 通用规则
- 关注表单验证、页面跳转、数据一致性
- 金额、账号等数字需验证正确性

---

### 1. 注册新用户
**步骤：**
1. 点击 "Register" 链接
2. 在 First Name 输入 "Test"
3. 在 Last Name 输入 "User"
4. 在 Address 输入 "123 Main St"
5. 在 City 输入 "New York"
6. 在 State 输入 "NY"
7. 在 Zip Code 输入 "10001"
8. 在 Phone 输入 "1234567890"
9. 在 SSN 输入 "123456789"
10. 在 Username 输入一个随机用户名（如 "testuser" 加当前时间戳）
11. 在 Password 输入 "Test1234!"
12. 在 Confirm 输入 "Test1234!"
13. 点击 "Register" 按钮

**结果预期：**
1. 显示注册成功页面
2. 出现 "Your account was created successfully" 或类似消息

---

### 2. 登录
**步骤：**
1. 如果已登录，先点击 "Log Out"
2. 在用户名输入框输入刚注册的用户名
3. 在密码输入框输入 "Test1234!"
4. 点击 "Log In" 按钮

**结果预期：**
1. 成功登录后显示账户概览页面
2. 页面显示用户的账户信息和余额

---

### 3. 查看账户详情
**步骤：**
1. 在账户概览页面点击任意账户链接
2. 确认跳转到账户详情页
3. 查看交易历史列表

**结果预期：**
1. 账户详情页显示账号、余额、可用余额
2. 交易列表正确加载（可能为空）
