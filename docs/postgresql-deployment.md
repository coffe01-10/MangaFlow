# PostgreSQL 部署与迁移前提

## 数据库准备

使用包含 contrib 模块的 PostgreSQL 16 或更高版本。迁移账号需要目标 schema 的建表、改表权限；扩展安装需要数据库 CREATE 权限（具体权限取决于服务器对 trusted extension 的配置）。如果迁移账号没有扩展安装权限，应由数据库管理员提前在目标数据库执行：

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

迁移 `20260901_23` 使用该扩展提供文本等值 GiST 运算符，为对账周期建立排他约束。扩展必须位于迁移连接的 `search_path` 内；隔离 schema 的验收环境应在该 schema 安装扩展，或明确包含管理员安装扩展的 schema。迁移不会删除共享扩展。

部署前检查：

```sql
SELECT name, installed_version FROM pg_available_extensions WHERE name = 'btree_gist';
SELECT extname, extnamespace::regnamespace FROM pg_extension WHERE extname = 'btree_gist';
SHOW search_path;
```

可用扩展列表为空时，先安装服务器的 contrib 包。存在但尚未安装时，由具备权限的账号安装。应用数据库必须通过 Alembic 升级；`Base.metadata.create_all()` 不创建 PostgreSQL 专属的对账排他约束，不能代替生产迁移。

## 枚举与旧数据

初始迁移 `949d8856e6a4` 显式创建并在降级时删除它拥有的六个枚举类型；`20260714_01` 只复用 `resolution`，支持分次执行升级。已在 head 的数据库不需要重建类型。降级会删除业务数据，只能在备份和明确的回滚方案下执行。

`20260716_09` 对 `B1`/`b1` 前缀使用相同匹配规则，避免 PostgreSQL 与 SQLite 对历史黑白样式产生不同回填结果。此修正只作用于尚未经过该版本的历史数据库；已升级数据库不会自动再次改写用户后来编辑的样式。
