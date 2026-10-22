# autoforge 独立重造设计

日期：2026-10-22
状态：待评审

## 1. 背景与目标

原项目（实习产出）= 生成外挂 + Docker 沙箱判卷 + LLM 修复闭环。本次重造：**去掉生成外挂，自研生成器**，使其成为完整独立的系统，并补齐缺失的评测体系。

一句话定位：

> 一句话需求 → 三段流水生成（plan → tests → code）→ 禁网 Docker 沙箱验收（pytest + 入口冒烟）→ LLM 结构化修复闭环 → 验收报告与成本账本。

设计哲学（对外叙事的锚点）：**不做更强的生成，做更硬的验证**。生成的瓶颈不在角色模拟，在于产出不可验证；确定性放在验证侧，LLM 放在提案侧。

硬指标：核心代码 ~1000 行（含评测 ~1500）；依赖仅 `openai / docker / pydantic / pyyaml`；全程 token 预算受控；每轮产物落盘可审计。

## 2. 非目标（YAGNI 边界）

- 不支持 Web/API 服务类项目（只做小 Python CLI/库工具）
- 不做逐文件 agent（write/read/run 工具循环）
- 不做多模型自动路由（评测换 `--model` 手动对比）
- 不做并行跑批（串行够用）
- 不引入任何多智能体框架依赖

## 3. 系统总览

```
需求 ─[plan]→ ProjectPlan JSON（模块 / 接口 / CLI 面 / behaviors 清单）
    ─[tests]→ 验收测试文件（测试即规格，oracle 先于实现）
    ─[code]→ 实现文件
    ─→ 沙箱判卷（pytest + 入口冒烟）
    ─→ 失败？→ 结构化 Failure[] + 修复记忆摘要
    ─→ [fix] FilePatch[] ─→ 完整性守卫（测试文件冻结）→ apply → 复验
    ─→ 循环至 全绿 / 预算尽 / 轮数尽
    ─→ runs/<ts>/ 落盘：generation/*.json + rounds/*.json + autoforge_report.json
```

架构上生成与修复是同一原语：**上下文 → `{"files": [{path, content}]}`**。生成是第 0 轮提案，修复是后续轮提案，共用 FilePatch schema、apply 机制、沙箱、预算闸门。

对齐文献词汇（评测/叙事用）：整体即 monitor–detect–diagnose–recover–verify 闭环；结构化失败报告 = 回路的信号质量，是系统的一等公民。

## 4. 仓库布局

```
autoforge/
├── pyproject.toml          # console_script: autoforge；依赖 4 个包
├── README.md               # 定位 + 用法 + 评测章节（数字表格）
├── .gitignore              # runs/ eval_results/ .wheels/ __pycache__/ ...
├── src/autoforge/
│   ├── cli.py              # run / judge / fix / eval 四个子命令
│   ├── llm.py              # 统一 OpenAI 兼容客户端
│   ├── schemas.py          # 数据契约（pydantic）
│   ├── generator.py        # 生成策略：plan_test_code | oneshot
│   ├── sandbox.py          # Docker 沙箱执行器（原 executor_sandbox 平移）
│   ├── loop.py             # 判卷-修复闭环（原 auto_fix_loop 平移+增强）
│   └── report.py           # 成本/可靠性账本（原 cost_report 泛化）
├── eval/
│   ├── seed_bugs/          # 种子 bug 基准：fixtures + make_bugs.py + manifest.json
│   ├── requirements/       # 端到端需求案例 + checklist.yaml
│   └── run_eval.py         # 跑批矩阵 → eval_results/<ts>/summary.md
└── tests/                  # 全离线单测 + docker-gated 集成测试
```

## 5. 模块设计

### 5.1 cli.py

子命令（退出码沿用原语义：all_green=0 / max_rounds_exhausted=1 / fixer_error=3 / budget_exhausted=4 / generation_failed=5）：

- `autoforge run "<idea>"`：全流程。flags：`--strategy plan_test_code|oneshot`、`--entry`、`--max-rounds`（默认 3）、`--budget-tokens`（默认 300k，全链路）、`--model`、`--timeout`、`--allow-test-edit`
- `autoforge judge <dir>`：只判卷出报告（不调修复 LLM）
- `autoforge fix <dir>`：对任意已有项目判卷+修复（原 --from-artifact 的正统化，证明闭环 generator-agnostic）
- `autoforge eval`：跑评测矩阵（见 §7）

### 5.2 llm.py — 统一客户端

- `chat_json(messages, max_tokens) → (parsed_obj, usage, meta)`；所有 LLM 调用（生成三段、修复、评测 judge）的唯一入口
- 特性（从原 llm_fixer 抽取泛化）：围栏/裸数组 JSON 容错；`finish_reason=length` 预算翻倍重试一次；usage 归集
- **meta 记录每次调用的 {ok, retries, parse_failed, tokens}**——可靠性层指标的数据源，零成本聚合
- 配置：`~/.autoforge/config.yaml`（api_key / base_url / model），环境变量 `AUTOFORGE_CONFIG` 覆盖路径。**不再借用原框架的配置文件**

### 5.3 schemas.py

保留：`Failure / TestReport / FixRequest / FilePatch / PatchList`（含路径安全校验，原样平移）。

新增 `ProjectPlan`：

```python
class ModuleSpec(BaseModel):
    path: str            # 模块文件路径
    responsibility: str
    public_interface: list[str]

class CliCommand(BaseModel):
    command: str
    args: str
    behavior: str

class ProjectPlan(BaseModel):
    name: str
    one_liner: str
    modules: list[ModuleSpec]
    cli_surface: list[CliCommand]
    behaviors: list[str]   # 验收行为清单——评测 checklist 的种子
```

### 5.4 generator.py — 策略模式

- `generate(strategy, idea) → GenerationResult(files, plan, usage, events)`
- `plan_test_code`（默认）：
  1. **plan**：idea → ProjectPlan JSON。schema 校验失败重试 1 次，仍失败**降级 oneshot** 并记 `degraded` 事件（生成永不硬崩）
  2. **tests**：plan → 测试文件 FilePatch[]。prompt 约束：pytest 风格、**仅标准库**（沙箱禁网装不了第三方包）、入口须 `--help` 可跑、每条 behavior 至少一个用例
  3. **code**：plan + 测试文件清单 → 实现 FilePatch[]
- `oneshot`：idea → 全部文件（含测试）。保留原因：与三段流水构成评测消融维度
- 产物写 `runs/<ts>/workspace/`，三段各自的 prompt/completion token 与解析事件落 `generation/events.json`

### 5.5 sandbox.py — 平移原 executor_sandbox

改动最小化（改包名/日志前缀）。保留全部核心语义：每次全新容器、只读挂载、容器级禁网、wheels 离线装 pytest、超时强杀、finally 必销毁、FAILURES/ERRORS 双区段解析、`failed>0 但解析为空` 的兜底 pseudo-Failure。

**约束**：生成的项目仅允许标准库（写进生成 prompt 与评测口径），因为沙箱禁网无法安装第三方依赖。`fix` 子命令对任意项目开放，但含第三方依赖的项目在禁网沙箱内判卷会因装不上依赖而失败——这是设计内的边界，不是缺陷。

### 5.6 loop.py — 闭环 + 两个新机制

原 `run_loop_structured` 平移，新增：

1. **修复记忆（Reflexion-lite）**：session 内维护 `attempts[{round, files_touched, outcome}]`，序列化为一段摘要注入 fix prompt，防止同坑反复修
2. **测试完整性守卫（防 spec gaming）**：生成阶段结束后记录测试文件内容 hash；修复阶段 `apply_patches` 拒绝写入被冻结的测试文件，违规记入 round 报告（`integrity_violations`）；`--allow-test-edit` 可关闭
3. **预算闸门升级**：从只管修复段 → 全链路（生成三段预扣 + 修复轮累计）

### 5.7 report.py

原 cost_report 泛化：聚合 `runs/*/`（生成段 + 各轮修复）出 Markdown 账本；新增可靠性聚合（JSON 解析率 / length 重试率 / 完整性违规数 / 沙箱故障率）。

## 6. 会话目录结构

```
runs/<YYYYmmdd-HHMMSS>/
├── workspace/                  # 生成的项目（fix 场景为输入项目的副本，原目录只读）
├── generation/
│   ├── plan.json               # ProjectPlan（oneshot 降级时缺省）
│   ├── tests_patch.json        # 测试文件补丁
│   ├── code_patch.json         # 实现补丁
│   └── events.json             # 三段调用事件（含 degraded）
├── rounds/round-N.json         # 每轮判卷/修复完整记录
└── autoforge_report.json       # 最终汇总（状态、token 总账、完整性事件）
```

## 7. 评测设计（三层）

| 层 | 内容 | 指标 |
|---|---|---|
| L1 可靠性 | 聚合全链路调用事件 | JSON 解析率、length 重试率、pytest 输出解析覆盖率、沙箱故障率、完整性违规数 |
| L2 修复能力 | 种子 bug 基准：5 个干净小工具 × 4 类注入 bug = 20 case | 修复成功率（首轮/最终）、轮次分布、token/case |
| L3 生成能力 | 8~10 个需求 × {oneshot, plan_test_code} × {无闭环, 有闭环} | 构建成功率（沙箱内 pytest 可收集且入口 `--help` 通过）、测试通过率、需求覆盖率（checklist 逐条）、token 成本 |

- **L2 构造**：`make_bugs.py` 对干净 fixture 按类型注入——import 损坏 / 入口空文件 / 逻辑反转 / 边界缺失；`manifest.json` 记 {case, bug_type}
- **L3 checklist**：每需求一份 `checklist.yaml`（条目源自需求的验收点，不源自生成器）；LLM-judge 初评 + 人工复核；judge 模型与被评系统模型隔离（prompt 与调用独立）
- 消融即副产品：策略/闭环/冒烟均为 CLI flag，`run_eval.py --matrix` 换参数自动出对比表
- 输出：`eval_results/<ts>/summary.md`（表格）+ `details.json`（原始数据）

## 8. 测试策略

- 单测全离线（无 Docker、无网络）：schema 校验、plan 解析与降级路径、prompt 组装、完整性守卫拒绝、sandbox 输出解析（原回归用例迁入）、账本聚合
- 集成测试：`@pytest.mark.skipif(无 Docker)`，沙箱真跑一轮
- 端到端冒烟：录制式 fake LLM client（不发真实请求）跑通 `autoforge run` 全流程

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 弱模型 plan 质量差 | schema 校验 + 重试 1 次 + 自动降级 oneshot（记事件） |
| 生成测试偏水（self-grading） | behaviors 逐条对应用例的 prompt 约束 + L3 外部 checklist 兜底 |
| 生成项目想用第三方库 | prompt 明令仅标准库 + 评测口径排除 + 沙箱禁网物理兜底 |
| 修复循环震荡不收敛 | 修复记忆 + max-rounds + 预算闸门三重限 |
| 开发/评测 token 花费 | 全链路预算闸门；评测先小矩阵试跑再放量 |

## 10. 里程碑

- **M1**：仓库骨架 + llm + schemas + generator(oneshot) + sandbox/loop 平移，`autoforge run` 端到端可用
- **M2**：plan_test_code 三段流水 + 完整性守卫 + 修复记忆 + report 泛化
- **M3**：评测三层落地 + README 评测章节（真实数字）+ 面试叙事文档

每个里程碑结束时系统可运行、可演示。
