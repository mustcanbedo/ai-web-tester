# MindCloudX 关键接口文档

> 本文档供 AI 自动化测试系统使用，描述平台核心 RESTful API 的请求方式、参数、返回结构及接口间数据逻辑关联。
> 所有接口信息均从源码提取。

---

## 一、全局约定

### 1.1 后端服务地址

| 服务名 | 生产环境 | 测试环境 | 用途 |
|--------|---------|---------|------|
| `manifoldtech_cloud` | `https://api.feimarobotics.com/v2` | `http://182.254.244.64:8081/v2` | 用户认证、短信验证 |
| `gs_manage` | `https://3dgs.afuav.com` | `https://3dgstest.afuav.com` | 项目管理、训练任务、文件存储 |

### 1.2 认证机制

- **Token 存储位置**：`localStorage.getItem('token')`
- **Token 注入方式**：Axios 请求拦截器自动在每个请求 Header 中添加 `token` 字段：
  ```
  headers: { token: '<JWT字符串>' }
  ```
- **Token 失效处理**：当响应 `status === 999` 时，拦截器自动调用 `authStore.logout()`，清除 `localStorage` 中的 `token` 和 `user`。

### 1.3 默认请求配置

| 配置项 | 值 |
|--------|-----|
| 默认 Content-Type | `application/json` |
| 请求超时 | `60000ms`（60 秒） |
| 成功状态码 | `status: 0`（GS 管理服务）/ `code: 0`（Cloud 服务） |

### 1.4 通用响应结构

**GS 管理服务（gs_manage）**：
```json
{
  "status": 0,
  "message": "success",
  "data": { ... }
}
```

**Cloud 服务（manifoldtech_cloud）**：
```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

---

## 二、用户认证模块（manifoldtech_cloud）

### 2.1 发送短信验证码

- **接口路径**：`POST {manifoldtech_cloud}/common/smsValid`
- **Content-Type**：`multipart/form-data`
- **描述**：向指定手机号发送 4 位数字验证码，支持登录、注册、重置密码、换绑手机号等场景。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `phone` | string | 是 | 纯数字手机号（不含区号前缀） |
| `action` | string | 是 | 场景标识：`"login_by_phone"` / `"register"` / `"reset_pwd"` / `"change_account"` |
| `dial_code` | string | 否 | 国际区号，如 `"+86"` |

**请求示例**：
```json
{
  "phone": "13800138000",
  "action": "login_by_phone",
  "dial_code": "+86"
}
```

**响应示例**：
```json
{
  "code": 0,
  "message": "success"
}
```

---

### 2.2 手机验证码登录

- **接口路径**：`POST {manifoldtech_cloud}/user/loginbyphone`
- **Content-Type**：`multipart/form-data`
- **Header**：`platform: MindCloudXAI`
- **描述**：通过手机号 + 短信验证码登录（自动注册）。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `phone` | string | 是 | 纯数字手机号 |
| `validate` | string | 是 | 4 位数字验证码 |
| `dial_code` | string | 否 | 国际区号，如 `"+86"` |

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 12345,
    "name": "user_138xxxx",
    "nickname": "用户昵称",
    "phone": "13800138000",
    "dial_code": "+86",
    "email": "",
    "avatar": "https://xxx/avatar.png",
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "password": ""
  }
}
```

**关键返回字段**：
- `data.token`：JWT Token，前端存入 `localStorage('token')`。
- `data.id`：用户 ID，用于后续判断任务创建者权限。
- `data` 整体存入 `localStorage('user')`。

---

### 2.3 密码登录

- **接口路径**：`POST {manifoldtech_cloud}/user/login4client`
- **Content-Type**：`multipart/form-data`
- **Header**：`platform: MindCloudXAI`
- **描述**：通过邮箱/手机号 + 密码登录。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 否 | 邮箱（与 phone 二选一） |
| `phone` | string | 否 | 手机号（与 email 二选一） |
| `password` | string | 是 | 密码 |
| `dial_code` | string | 否 | 国际区号 |

**响应结构**：同 2.2。

---

### 2.4 获取用户信息

- **接口路径**：`GET {manifoldtech_cloud}/user/info`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`
- **描述**：获取当前登录用户的详细信息。

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 12345,
    "name": "user_138xxxx",
    "nickname": "用户昵称",
    "phone": "13800138000",
    "dial_code": "+86",
    "email": "user@example.com",
    "avatar": "https://xxx/avatar.png",
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "password": ""
  }
}
```

---

### 2.5 用户注册

- **接口路径**：`POST {manifoldtech_cloud}/user/register`
- **Content-Type**：`multipart/form-data`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 否 | 邮箱（与 phone 二选一） |
| `phone` | string | 否 | 手机号（与 email 二选一） |
| `password` | string | 是 | 密码 |
| `validate` | string | 是 | 验证码 |
| `dia_code` | string | 否 | 国际区号（注意：此处字段名为 `dia_code`） |

---

### 2.6 重置密码

- **接口路径**：`POST {manifoldtech_cloud}/user/resetPwd`
- **Content-Type**：`multipart/form-data`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` / `phone` | string | 是 | 账号（二选一） |
| `password` | string | 是 | 新密码 |
| `validate` | string | 是 | 验证码 |

---

### 2.7 修改密码

- **接口路径**：`POST {manifoldtech_cloud}/user/editPwd`
- **Content-Type**：`multipart/form-data`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` / `phone` | string | 是 | 账号 |
| `password` | string | 是 | 原密码 |
| `new_password` | string | 是 | 新密码 |

---

### 2.8 更新用户信息

- **接口路径**：`POST {manifoldtech_cloud}/user/updateInfo`
- **Content-Type**：`multipart/form-data`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `nickname` | string | 是 | 昵称 |
| `sex` | number | 是 | 性别（0=男，1=女） |
| `company` | string | 是 | 公司 |
| `address` | string | 是 | 地址 |
| `comment` | string | 是 | 备注 |

---

### 2.9 上传用户头像

- **接口路径**：`POST {manifoldtech_cloud}/user/uploadIcon`
- **Content-Type**：`multipart/form-data`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `icon` | File | 是 | 头像图片文件 |

---

### 2.10 Google 登录

- **接口路径**：`POST {manifoldtech_cloud}/user/loginByGoogle`
- **Content-Type**：`multipart/form-data`
- **Header**：`platform: MindCloudXAI_Google`
- **描述**：通过 Google OAuth 授权码完成登录（自动注册）。前端通过 Google OAuth 弹窗获取 `code`，再提交给此接口换取用户信息和 Token。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | string | 是 | Google OAuth 授权码 |

**响应结构**：同 2.2。

---

### 2.11 发送邮箱验证码

- **接口路径**：`POST {manifoldtech_cloud}/common/emailValid`
- **Content-Type**：`multipart/form-data`
- **描述**：向指定邮箱发送验证码，支持注册、重置密码、换绑邮箱等场景。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 是 | 邮箱地址 |
| `action` | string | 是 | 场景标识：`"register"` / `"reset_pwd"` / `"change_account"` |

**响应示例**：
```json
{
  "code": 0,
  "message": "success"
}
```

---

### 2.12 校验验证码

- **接口路径**：`POST {manifoldtech_cloud}/common/checkValid`
- **Content-Type**：`multipart/form-data`
- **描述**：校验手机号或邮箱的验证码是否正确，用于注册和重置密码流程中的前置验证。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `phone` 或 `email` | string | 是 | 手机号或邮箱（二选一） |
| `action` | string | 是 | 场景标识：`"register"` / `"reset_pwd"` |
| `validate` | string | 是 | 验证码 |

**响应示例**：
```json
{
  "code": 0,
  "message": "success"
}
```

---

### 2.13 换绑账号

- **接口路径**：`POST {manifoldtech_cloud}/user/changeAccount`
- **Content-Type**：`multipart/form-data`
- **描述**：更换绑定的手机号或邮箱。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `phone` 或 `email` | string | 是 | 新手机号或新邮箱（二选一） |
| `validate` | string | 是 | 新手机号/邮箱收到的验证码 |
| `dial_code` | string | 否 | 国际区号（换绑手机号时使用） |

**响应结构**：同 2.2。

---

### 2.14 刷新用户缓存

- **接口路径**：`POST {manifoldtech_cloud}/user/refreshCache`
- **Content-Type**：`multipart/form-data`
- **Header**：`token: <JWT>`
- **描述**：刷新服务端用户信息缓存。

**响应示例**：
```json
{
  "code": 0,
  "message": "success"
}
```

---

## 三、项目管理模块（gs_manage）

### 3.1 获取项目列表

- **接口路径**：`POST {gs_manage}/api/v1/project/list`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`
- **描述**：分页获取当前用户的项目列表，支持关键词搜索。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `page` | number | 是 | 页码，从 1 开始 |
| `size` | number | 是 | 每页条数（默认 8） |
| `keyword` | string | 否 | 搜索关键词（匹配项目名称） |
| `project_code` | string | 否 | 按项目编码精确查询 |
| `project_name` | string | 否 | 按项目名称查询 |

**请求示例**：
```json
{
  "page": 1,
  "size": 8,
  "keyword": "测试"
}
```

**响应示例**：
```json
{
  "status": 0,
  "message": "success",
  "data": {
    "list": [
      {
        "id": 101,
        "project_name": "测试项目A",
        "project_code": "abc123def456",
        "status": 1,
        "file_size": 498073600,
        "file_path": "/data/projects/abc123",
        "train_count": 3,
        "task_count": 5,
        "creator": 12345,
        "updater": 12345,
        "updated_at": 1700000000,
        "created_at": 1699000000,
        "cover_image": "https://xxx/cover.png"
      }
    ],
    "page": 1,
    "size": 8,
    "total": 15
  }
}
```

**关键字段说明**：
- `id`：项目 ID，用于后续编辑、删除、查询任务。
- `project_code`：项目唯一编码，前端仅显示前 6 位。
- `task_count`：该项目下的训练任务总数。
- `cover_image`：项目封面图 URL，可能为 `null`。

---

### 3.2 编辑项目（重命名）

- **接口路径**：`POST {gs_manage}/api/v1/project/edit`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 项目 ID |
| `project_name` | string | 是 | 新的项目名称 |

**请求示例**：
```json
{
  "id": 101,
  "project_name": "新项目名称"
}
```

**响应示例**：
```json
{
  "status": 0,
  "message": "success"
}
```

---

### 3.3 删除项目

- **接口路径**：`POST {gs_manage}/api/v1/project/delete`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 项目 ID |

**响应示例**：
```json
{
  "status": 0,
  "message": "success"
}
```

---

## 四、训练任务模块（gs_manage）

### 4.1 获取任务列表

- **接口路径**：`POST {gs_manage}/api/v1/task/list`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`
- **描述**：分页获取指定项目和任务类型下的训练任务列表。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `page` | number | 是 | 页码，从 1 开始 |
| `size` | number | 是 | 每页条数（默认 8） |
| `task_type` | number | 是 | 任务类型：`1`=点云, `2`=3DGS, `3`=CAD, `4`=Mesh |
| `project_id` | number | 是 | 所属项目 ID（来自项目列表的 `id` 字段） |
| `keyword` | string | 否 | 搜索关键词（匹配任务名称/编号） |
| `status` | number | 否 | 状态过滤：`1`=队列中, `2`=进行中, `3`=失败, `4`=成功 |
| `status_list` | string | 否 | 进度状态多个，逗号分隔，如 `"50,51,52"` |

**请求示例**：
```json
{
  "page": 1,
  "size": 8,
  "task_type": 2,
  "project_id": 101,
  "keyword": "",
  "status": 4
}
```

**响应示例**：
```json
{
  "status": 0,
  "message": "success",
  "data": {
    "list": [
      {
        "id": 501,
        "project_id": 101,
        "project_name": "测试项目A",
        "task_name": "训练任务1",
        "task_code": "task_abc123",
        "status": 4,
        "progress": 100,
        "progress_status": 99,
        "iterations": 30000,
        "train_progress": "",
        "task_path": "/data/tasks/501",
        "splat_path": "https://xxx/result.splat",
        "splat_zip_path": "https://xxx/result_splat.zip",
        "splat_size": 52428800,
        "ply_path": "https://xxx/result.ply",
        "ply_zip_path": "https://xxx/result_ply.zip",
        "ply_size": 104857600,
        "file_zip": "https://xxx/source.zip",
        "start_time": 1699000100,
        "end_time": 1699003700,
        "download_time": 120,
        "train_time": 3000,
        "splat_time": 300,
        "splat_upload_time": 180,
        "error_message": "",
        "creator": 12345,
        "updater": 12345,
        "updated_at": "",
        "created_at": 1699000000,
        "user_name": "测试用户",
        "mobile": "138****0000",
        "cover_image": "https://xxx/cover.png",
        "input_config": {
          "quality": "fast"
        },
        "output_result": {
          "splat": {
            "url": "https://xxx/result.splat",
            "zip_url": "https://xxx/result_splat.zip"
          },
          "ply": {
            "url": "https://xxx/result.ply",
            "zip_url": "https://xxx/result_ply.zip"
          },
          "spx": {
            "url": "https://xxx/result.spx"
          },
          "potree": {
            "url": "https://xxx/potree/cloud.js",
            "hierarchy_url": "https://xxx/potree/hierarchy.bin",
            "octree_url": "https://xxx/potree/octree.bin"
          },
          "pointcloud": {
            "url": "https://xxx/pointcloud.las",
            "zip_url": "https://xxx/pointcloud.zip",
            "size": 209715200
          },
          "cad": {
            "url": "https://xxx/cad_model.obj",
            "zip_url": "https://xxx/cad_model.zip"
          },
          "mesh": {
            "url": "https://xxx/mesh.ply",
            "zip_url": "https://xxx/mesh.zip",
            "size": 52428800
          },
          "texture": {
            "url": "https://xxx/texture.png",
            "zip_url": "https://xxx/texture.zip",
            "zip_size": 10485760
          },
          "obj": {
            "url": "https://xxx/model.obj",
            "zip_url": "https://xxx/model_obj.zip",
            "size": 31457280
          },
          "tiles_3d": {
            "url": "https://xxx/tileset.json",
            "zip_url": "https://xxx/tiles3d.zip",
            "zip_size": 104857600,
            "json": { "asset": {}, "root": {} }
          }
        },
        "sparse": {
          "cameras": "https://xxx/sparse/cameras.bin",
          "images": "https://xxx/sparse/images.bin",
          "points3D": "https://xxx/sparse/points3D.bin"
        },
        "extra_fields": {
          "colmap_path": "https://xxx/colmap/"
        },
        "model_config": null
      }
    ],
    "page": 1,
    "size": 8,
    "total": 3
  }
}
```

**关键字段说明**：
- `id`：任务 ID，用于编辑、删除、重试。
- `task_code`：任务唯一编码，用于查看器 URL 参数。
- `status`：`1`=队列中, `2`=进行中, `3`=失败, `4`=成功。
- `progress`：训练进度百分比（0-100）。
- `progress_status`：细粒度进度状态码。
- `creator`：创建者用户 ID，用于判断编辑权限。
- `output_result`：训练输出结果，包含各格式文件的 URL。各子字段按任务类型不同：
  - `splat` / `ply` / `spx`：3DGS 类型
  - `potree` / `pointcloud`：点云类型
  - `cad`：CAD 类型
  - `mesh` / `texture` / `tiles_3d`：Mesh 类型（`tiles_3d.json` 为内联 tileset.json，子 tile URI 已签名）
  - `obj`：通用 OBJ 格式
- `input_config`：训练输入配置（quality / resolution / loop / text_true 等）。

---

### 4.2 创建任务（加入队列）

- **接口路径**：`POST {gs_manage}/api/v1/task/add`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | number | 是 | 所属项目 ID |
| `task_type` | number | 否 | 任务类型（默认 `2`）：`1`=点云, `2`=3DGS, `3`=CAD, `4`=Mesh |
| `task_name` | string | 否 | 任务名称（2-50 字符） |
| `input_config` | object | 否 | 训练配置参数 |

**`input_config` 按任务类型不同**：

**3DGS（task_type=2）**：
```json
{
  "quality": "fast"
}
```
- `quality` 可选值：`"ultra fast"` / `"fast"` / `"medium"`

**点云（task_type=1）**：
```json
{
  "resolution": 0,
  "loop": true,
  "bundle": true,
  "mvobj": true
}
```

**Mesh（task_type=4）**：
```json
{
  "text_true": 0
}
```
- `text_true`：纹理贴图开关，`0`=关闭, `1`=开启

**完整请求示例（3DGS）**：
```json
{
  "project_id": 101,
  "task_type": 2,
  "task_name": "我的训练任务",
  "input_config": {
    "quality": "fast"
  }
}
```

**响应示例**：
```json
{
  "status": 0,
  "message": "success",
  "data": {
    "id": 502,
    "task_code": "task_new123"
  }
}
```

---

### 4.3 编辑任务（重命名）

- **接口路径**：`POST {gs_manage}/api/v1/task/edit`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 任务 ID |
| `task_name` | string | 是 | 新的任务名称 |

**请求示例**：
```json
{
  "id": 501,
  "task_name": "重命名后的任务"
}
```

**响应示例**：
```json
{
  "status": 0,
  "message": "success"
}
```

---

### 4.4 编辑任务详情（模型配置 + 封面图）

- **接口路径**：`POST {gs_manage}/api/v1/task/edit`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`
- **描述**：与 4.3 共用同一 URL，但传递更多字段。用于模型编辑器保存配置。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 任务 ID |
| `task_name` | string | 否 | 任务名称 |
| `cover_image` | string | 否 | 封面图 URL（COS 上传后的 URL） |
| `model_config` | object | 否 | 3D 模型配置 |

**`model_config` 结构**：
```json
{
  "pose_list": [
    {
      "position": { "x": 0, "y": 0, "z": 5 },
      "target": { "x": 0, "y": 0, "z": 0 },
      "fov": 60
    }
  ],
  "billboards": [
    {
      "id": "bb1",
      "label": "标注1",
      "name": "info",
      "position": { "x": 1, "y": 2, "z": 3 },
      "content": "这是一个标注",
      "contentType": "text"
    }
  ],
  "rotation": [0, 0, 0],
  "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
  "splatUrl": "https://xxx/result.splat",
  "collisionUrl": ""
}
```

---

### 4.5 获取单个任务详情

- **接口路径**：`POST {gs_manage}/api/v1/task/get`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 任务 ID |

**响应**：`data` 为单个 `GsTask` 对象，结构同 4.1 列表中的单个任务项。

---

### 4.6 获取任务详情（通过 task_code，公开访问）

- **接口路径**：`POST {gs_manage}/api/v1/redirect`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`（可选）
- **描述**：通过 `task_code` 获取任务详情。模型查看器和编辑器使用此接口。

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_code` | string | 是 | 任务唯一编码 |

**响应**：`data` 为单个 `GsTask` 对象。

---

### 4.7 删除任务

- **接口路径**：`POST {gs_manage}/api/v1/task/delete`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | number | 是 | 任务 ID |

**响应示例**：
```json
{
  "status": 0,
  "message": "success"
}
```

---

### 4.8 重试任务

- **接口路径**：`POST {gs_manage}/api/v1/task/retry`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | number | 是 | 任务 ID（注意字段名为 `task_id` 而非 `id`） |

**响应示例**：
```json
{
  "status": 0,
  "message": "success"
}
```

---

## 五、文件存储模块（gs_manage）

### 5.1 获取 COS 临时密钥

- **接口路径**：`GET {gs_manage}/api/v1/common/GetCosTemporaryKey`
- **Content-Type**：`application/json`
- **Header**：`token: <JWT>`
- **描述**：获取腾讯云 COS 临时访问凭证，用于客户端直传文件（如上传模型封面图）。

**响应示例**：
```json
{
  "status": 0,
  "message": "success",
  "data": {
    "appid": "125xxxxxxx",
    "bucket": "bucket-name-125xxxxxxx",
    "region": "ap-guangzhou",
    "credentials": {
      "Response": {
        "Credentials": {
          "TmpSecretId": "AKIDxxxxxxxx",
          "TmpSecretKey": "xxxxxxxx",
          "Token": "xxxxxxxx"
        },
        "Expiration": "2024-01-01T12:00:00Z",
        "ExpiredTime": 1704110400,
        "RequestId": "req-xxxxx"
      }
    }
  }
}
```

**使用场景**：模型编辑器保存封面图时：
1. 调用此接口获取临时密钥。
2. 使用 COS SDK 将封面图上传到 `{bucket}/{region}` 的路径 `public/cover_image/cover_{uuid}.png`。
3. 上传完成后获得 COS URL，传给 `taskEditDetailApi`（4.4）的 `cover_image` 参数。

---

## 六、接口数据逻辑关联

### 6.1 登录 → 后续所有请求

```
loginByPhoneApi → 返回 data.token
                 ↓
            存入 localStorage('token')
                 ↓
       Axios 拦截器自动注入 headers.token
                 ↓
         所有后续 API 请求均携带 token
```

### 6.2 项目列表 → 训练任务列表

```
projectListApi → data.list[].id (project_id)
                              ↓
                    taskListApi({ project_id })
```
- 前端从项目列表获取 `id`，作为 `taskListApi` 的 `project_id` 参数。
- 项目的 `task_count` 字段应与该项目下所有任务类型的任务总数一致。

### 6.3 创建任务 → 任务列表刷新

```
taskAddApi({ project_id, task_type, task_name, input_config })
                 ↓
          返回 status: 0
                 ↓
     前端调用 taskListApi 刷新列表
                 ↓
     新任务 status=1（队列中）出现在列表中
```

### 6.4 任务详情 → 模型查看/编辑

```
taskListApi → data.list[].task_code
                        ↓
    前端按任务类型跳转不同页面：
    ├─ 3DGS  → /gs/editor?taskId={task_code}（新标签页）
    ├─ 点云   → /app/pointcloud-viewer?taskId={task_code}&from={当前路径}
    ├─ CAD   → /app/cad-viewer?taskId={task_code}&from={当前路径}
    └─ Mesh  → /mesh/editor?taskId={task_code}（新标签页）
                        ↓
       taskShareDetailApi({ task_code }) → 获取完整任务数据
                        ↓
            读取 output_result 中的文件 URL 加载模型
```

### 6.5 模型编辑 → 封面图上传 → 保存配置

```
getCosTemKey → data.credentials (临时密钥)
                     ↓
        COS SDK 上传封面图 → 获得 cover_image URL
                     ↓
   taskEditDetailApi({ id, cover_image, model_config })
                     ↓
              保存成功，返回 status: 0
```

### 6.6 训练列表 → 查看器/编辑器

```
taskListApi → data.list[].task_code
                        ↓
    前端按任务类型跳转不同页面：
    ├─ 3DGS  → /gs/editor?taskId={task_code}（新标签页）
    ├─ 点云   → /app/pointcloud-viewer?taskId={task_code}&from={当前路径}
    ├─ CAD   → /app/cad-viewer?taskId={task_code}&from={当前路径}
    └─ Mesh  → /mesh/editor?taskId={task_code}（新标签页）
                        ↓
       taskShareDetailApi({ task_code }) → 获取完整任务数据
                        ↓
            读取 output_result 中的文件 URL 加载模型
```

### 6.7 任务状态流转

```
taskAddApi (创建) → status=1 (队列中)
                          ↓
              后端自动调度 → status=2 (进行中)
                          ↓
             训练完成 → status=4 (成功)
             训练出错 → status=3 (失败)
                          ↓
             失败时可调用 taskRetryApi → 重新 status=1
```

### 6.8 前端轮询机制

- 训练列表页面挂载后，每 **10 秒**调用一次 `taskListApi` 刷新数据。
- 使用 `fetchVersion` 防止快速切换 Tab 时旧请求覆盖新数据。
- 组件卸载时停止轮询。

---

## 七、任务状态码参考

### 7.1 任务主状态（status）

| 值 | 含义 | 前端显示 |
|----|------|---------|
| 1 | 队列中 | 队列中 |
| 2 | 进行中 | 进行中 |
| 3 | 失败 | 失败 |
| 4 | 成功 | 成功 |

### 7.2 任务类型（task_type / DataType 枚举）

| 值 | 含义 | Tab 名称 |
|----|------|---------|
| 1 | 点云 (POINTCLOUD) | 点云 |
| 2 | 3DGS (THREE_DGS) | 3DGS |
| 3 | CAD | CAD |
| 4 | Mesh | Mesh |
