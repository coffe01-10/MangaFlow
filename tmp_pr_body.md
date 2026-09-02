## 概述

V02-22B(与 #81 关联):按 `docs/v02-character-model-package-contract.md` 实现角色模型包数据层、服务/API 与生成版本锁定。基线 `25a4754`(origin/master 最新)。

## 实现内容

1. **Alembic + ORM**:迁移 `20260902_25_character_model_packages`(`20260901_24` → head):四表(包/版本/参考图关系/服装关系),循环外键两阶段创建(PG 先建表后 `ADD CONSTRAINT`,SQLite 内联前向引用),部分唯一索引双库同 `WHERE`,schema 所有权校验;回填每存量 Character 兼容 ACTIVE 包 + V1 DRAFT(不发布、指针 NULL、只 INSERT 不改写既有行、不复制图片文件),`angle→role` 确定性映射 + 碰撞 `-{n}` 后缀,软删 Asset 的绑定跳过、服装按 `created_at` 序复制且 `is_default=False`;downgrade 四条拒绝条件 + 子表优先删除(PG 先解除指针约束)。ORM 与迁移的索引定义统一为具名唯一索引,保证 `create_all`/迁移两路径 schema 一致。
2. **服务 + API**(`app/services/character_packages.py` + `api/routes/character_packages.py`):契约 §9.1 全部端点;包行锁 + 保存点重试 + compare-and-increment 乐观令牌(规格编辑用 `package.version`,关系变更用父 DRAFT 的 `version`,两者均先校验再递增);发布/激活/归档/恢复共享包锁并在锁内重验目标状态与发布指针(防止指针指向 ARCHIVED 版本);发布单事务(规格冻结 + READY + published_at + 指针同事务),零参考关系 422;派生复制 base 全部关系并把工作集整体重置、`package.version` 递增;DRAFT 删除保留最后版本。
3. **完整度 + diff**:读取路径确定性计算(READY+ 读 `spec_snapshot`、DRAFT 读包工作集,20/40/20/20,缺失项 `{code, field, message, suggestion}`),不落库、不进门禁;版本 diff 按 `(role, label)` 逐槽与规格字段级对比。
4. **生成链路**:逐出镜角色按"显式 `package_version_id`(跨角色/跨项目 409,DRAFT 422,ARCHIVED 允许)> ACTIVE 包发布指针(READY/IN_PRODUCTION)> legacy"解析;命中版本的字符在排队时冻结 `prompt_snapshot["character_packages"]`(包 ID、版本号、规格全文、`spec_fingerprint`、实际 Asset ID、风格事实),首个引用版本同事务条件置入 `IN_PRODUCTION`(默认路径重解析一次,仍不可用 409);`prompt_snapshot` 与 `input_versions`(紧凑镜像)一致(Worker 在编译替换前捕获,合并时以保留值为准);Worker 只消费排队快照(校验 Asset 存活 + 版本行存在),bible 用冻结名称/规格替换现场读取;门禁 `MISSING_OUTFIT_ASSIGNMENT` 增加包默认服装替代满足路径(`start_batch` 默认继承上下文与 `create_page_candidate` 完整解析上下文一致);发布不触发 NEEDS_REVIEW。
5. **删除守卫**:资产软删清除 DRAFT 关系行(+ 版本令牌递增)、READY+ 保留冻结事实;`bind_reference` 跨角色换绑/绑定前检查其他角色的包版本引用(409);`delete_outfit` 版本引用前置 409;`delete_candidate`(素材候选)同步清理 DRAFT 关系。
6. **修复/升清**:PAGE_REPAIR/PAGE_UPSCALE 候选继承原候选完整快照(含 character_packages),不重新解析(契约 §8.6-3)。
7. **文档**:`docs/data-model.md`、`docs/architecture.md` 同步为"V02-22B 已实现"。

## 测试

- 新增 `tests/test_character_packages.py`(23 项)+ `test_migrations.py` 迁移往返/回填/拒绝降级(4 项):覆盖 PKG-S1~S13(PKG-S14 见下)。
- 定向验证 + 全量回归:`pytest tests/`(不含 e2e)**583 passed, 27 skipped**;`npm run check` 各步:check:neutrality、eslint、ruff、vitest(239)、pytest 全部通过;web 生产构建在 junction worktree 下 Turbopack 拒绝 node_modules 软链(已知环境问题),用 `next build --webpack` 验证通过(CI 无 junction,用默认命令)。

## 已知未验证边界(NOT RUN)

- PKG-S14:真实 PostgreSQL 升降级与并发(两阶段 FK、RESTRICT、`FOR UPDATE` 包锁竞争)——本环境无 PostgreSQL,**NOT RUN**;SQLite 往返不替代。
- Redis/RQ 多 Worker 真实集成:NOT RUN。
- 真实供应商调用(generate-views/expressions 未实现):NOT RUN。
- 浏览器 E2E/UI 验收(V02-23B 范围):NOT RUN。
