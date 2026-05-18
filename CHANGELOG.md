## 2.0.0-sample (2026-05-18)

### BREAKING

- **Python 最低要求 3.12**（原 3.9），运行旧 Python 版本将无法导入
- **Pydantic 升级至 v2**：`@validator` → `@field_validator`、`class Config` → `model_config`、`.dict()` → `.model_dump()`
- **FastAPI 生命周期改为 `lifespan`**：`@app.on_event("startup"/"shutdown")` 在 FastAPI 新版本中已弃用
- **移除全局 `ObjectManager`（`global_om`）**：依赖通过 `app.state` / `Depends` 注入

### Feat

- 配置层引入 `pydantic-settings`，统一环境变量与 `pyproject.toml` 的项目级配置
- Redis 单连接与哨兵客户端抽出公共 `_BaseRedisClient` 基类，共享重连状态机
- Kafka 客户端抽出 `_BaseKafkaClient` 基类，复用生命周期
- 新增 `tool.ruff` 配置（target-version = py312）

### Refactor

- 依赖栈升级到 3.12 兼容的最新主版本：
  - `aioredis 2.0.1` → `redis>=5.1.1`（`redis.asyncio`）
  - `aiomysql` → `tortoise-orm[asyncmy]>=0.21.7`
  - `async_timeout` 第三方库 → 内置 `asyncio.timeout()`
  - `fastapi>=0.115`、`pydantic>=2.9`、`uvicorn>=0.32` 等
- 配置加载从 `tomlkit + json` 改为内置 `tomllib`
- `decorators.Action` 5 个 HTTP 方法工厂收敛到统一的 `_make`（339 行 → 100 行）
- 调度器去除 `import` 时的副作用，单一 `AsyncIOScheduler` 由 lifespan 管理
- 全量 typing 现代化：`Optional[X]` → `X | None`，`Dict/List/Set/Tuple/Type` → 内建泛型
- 删除空占位文件 `fastapi_tools/routing.py`

### Fix

- `routers/resource/models.py` 误用 `from dns.enum import IntEnum`，改回 `from enum import IntEnum`
- `mqtt_tools/kafka/KafkaClient.py` `if __name__ == '__main__':` 块内顶层 `await` 导致的 `SyntaxError`
- Dockerfile 升级到 `python:3.12-slim-bookworm`，并适配新的 Debian sources 格式

### Docs

- CHANGELOG 编码从 GBK 统一为 UTF-8

---

## 1.2.5-sample (2023-02-21)

### Feat

- 增加 kafka 连接
- 增加 redis 哨兵模式连接

## 1.2.4-sample (2023-02-17)

### Fix

- 增加异步定时任务

## 1.2.3-sample (2022-02-16)

### Fix

- 添加 FastAPI 自动生成基础方法

## 1.2.2-sample (2022-02-14)

### Fix

- 补充例子
- 修改 toml 文件中关于 database 的配置

## 1.2.1-sample (2022-02-14)

### Fix

- 修复预编译时忽略 pyproject.toml 的问题
- 修改配置从 toml 中提取
- 修复 setup.py 中不能自动适配 version 的问题

## 1.2.0-sample (2022-02-14)

### Fix

- 新增 settings 中读取 toml 项目配置
- 添加例子

### Feat

- 添加单元测试目录结构

## 1.1.1-sample (2021-12-16)

### Fix

- 变更 version 配置
- 删除 toml 包

### Refactor

- 变更 settings 文件的编码格式

## 1.1.0-sample (2021-12-16)

### Refactor

- FastAPI 文档自动适配 version

### Feat

- 添加 version 配置，添加工具包

## 1.0.0-sample (2021-12-15)

### Fix

- 修改字段验证器错误提示
- 重构项目结构
- 添加 Linux 系统下打包 uvloop
- 修复部分写法问题
- 更改项目结构
- 完善 CBV 调度
- 修改 ShortUIDField
- 添加序列化示例代码
- 修复 setup 打包时 site-packages 扫描不全的问题
- 修复 site-packages 在 Linux 下不能正确匹配的问题
- 修复 FastAPI 缺少依赖的问题

### Refactor

- 修改 tag 规则
- 修改 all 参数，添加 uvloop 依赖

### Perf

- 响应使用 orjson

### Feat

- 添加 FastAPI 的 CBV 实现
- 添加数据库迁移能力
- 搭建数据库连接与 setup 编译框架
- FastAPI 基本架构
- Init
