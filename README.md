# autoforge

> 一句话需求 → 生成 → 禁网沙箱验收 → LLM 修复闭环。

**设计哲学：不做更强的生成，做更硬的验证。** LLM 生成的代码不可信，
所以确定性放在验证侧——每个产物都要在禁网 Docker 沙箱里过
pytest + 入口冒烟，失败结构化回传 LLM 迭代修复，直到全绿、或预算/轮数耗尽。

```
需求 ─[生成]→ 项目文件 ─→ 禁网沙箱判卷（pytest + 入口冒烟）
                         ─→ 失败？→ 结构化失败报告 → LLM 补丁 → 复验 ─┐
                         └──────────────────────────────────────────────┘
```

生成与修复是同一原语（上下文 → `{"files": [...]}`），生成即第 0 轮提案。

## 安装

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.yaml ~/.autoforge/config.yaml  # 填入你的 OpenAI 兼容端点
```

沙箱依赖 Docker；首次运行会自动下载 pytest wheels 与 python:3.11-slim 镜像。
生成的项目仅允许标准库（沙箱禁网，第三方包装不上——这是设计内边界）。

## 使用

```bash
autoforge run "一个记账 CLI，支持 add/list/summary 子命令"   # 全流程
autoforge judge ./some_project                              # 只判卷（pytest+冒烟）
autoforge fix ./some_project                                # 判卷+修复（任意项目）
```

常用参数：`--max-rounds`（默认 3）、`--budget-tokens`（默认 300k，生成+修复全链路共享，
生成段预扣）、`--model`、`--timeout`（沙箱单命令秒数）、`--entry`（覆盖入口冒烟命令）、
`--config`（配置文件路径，默认 `~/.autoforge/config.yaml`）。

会话产物落 `runs/<时间戳>/`：`workspace/`（生成项目）、`generation/events.json`、
`rounds/round-N.json`（每轮判卷与补丁）、`events.json`（全链路 LLM 调用事件）、
`autoforge_report.json`（最终报告）。

## 退出码

`run` / `fix` 状态退出码：

| 码 | 含义 |
|---|---|
| 0 | all_green——验收通过 |
| 1 | max_rounds_exhausted——轮数用尽仍有失败 |
| 2 | 前置失败——输入目录不存在 / 配置缺失 |
| 3 | fixer_error——修复调用失败 |
| 4 | budget_exhausted——token 预算用尽 |
| 5 | generation_failed——生成失败 |
| 6 | loop_error——闭环执行异常（含沙箱不可用） |

`judge` 单独一档：0 = 判卷完成（结果看输出/`--json`，测试失败也算"完成"，
沿用"捕获即成功"语义）、1 = pytest 超时、2 = 目录不存在/沙箱不可用。

## 测试

```bash
.venv/bin/python -m pytest tests/ -v          # 离线单测 + 端到端（Docker 运行时）
.venv/bin/python -m pytest tests/ -v --ignore=tests/test_e2e_fake.py  # 纯离线
```

端到端测试用脚本化 fake LLM 走通全链路（生成带缺陷项目 → 沙箱判卷失败 →
修复 → 复验全绿），沙箱侧为真实 Docker 容器；Docker 不可用时自动跳过。

## 路线

- [x] M1 骨架 + oneshot 生成 + 端到端闭环
- [ ] M2 三段流水生成（plan → tests → code）+ 测试完整性守卫 + 修复记忆
- [ ] M3 三层评测（可靠性 / 种子 bug 修复基准 / 端到端需求基准）

设计文档见 `docs/superpowers/specs/`。
