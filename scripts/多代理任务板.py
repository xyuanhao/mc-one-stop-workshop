#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多代理任务板.py —— 派子代理前检查分工，交付前检查完成证据。

用法：
  python 多代理任务板.py init 协作计划.json --project 我的模组
  python 多代理任务板.py check 协作计划.json
  python 多代理任务板.py check 协作计划.json --delivery
  python 多代理任务板.py --selftest

退出码：0=通过 / 1=计划或交付门禁未通过 / 2=参数、路径或 JSON 错误。
本脚本不启动代理、不修改工程；它验证的是协作契约，不替代最终构建与进游戏实测。
"""
import argparse
import copy
import json
import re
import sys
from pathlib import Path, PurePosixPath

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STATUSES = {"pending", "in_progress", "completed", "blocked"}


def template(project):
    return {
        "schema_version": 1,
        "project": project,
        "tasks": [
            {
                "id": "research",
                "title": "只读调查与接口建议",
                "owner": "agent:research",
                "readonly": True,
                "depends_on": [],
                "acceptance": ["列出真实文件、行号与结论依据"],
            },
            {
                "id": "implement",
                "title": "实现独立工作包",
                "owner": "agent:implement",
                "writes": ["src/main/java/com/example/mod/feature/"],
                "depends_on": ["research"],
                "acceptance": ["工作包验收命令通过", "未越界修改其他文件"],
            },
            {
                "id": "integrate",
                "title": "主代理集成与整体验证",
                "owner": "main",
                "writes": ["src/main/java/com/example/mod/registry/"],
                "depends_on": ["implement"],
                "acceptance": ["整工程 build 通过", "交付门禁通过"],
            },
        ],
    }


def norm_write(value):
    """返回 (规范化路径, 是否目录)；非法路径抛 ValueError。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("写路径必须是非空字符串")
    raw = value.strip().replace("\\", "/")
    is_dir = raw.endswith("/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("写路径必须相对工程根，不能是绝对路径")
    parts = raw.split("/")
    if any(p in ("", ".", "..") for p in parts if p != ""):
        raise ValueError("写路径不能含 '.'、'..' 或重复分隔符")
    if ".." in parts:
        raise ValueError("写路径不能越过工程根")
    pure = PurePosixPath(raw)
    if any(p == ".." for p in pure.parts):
        raise ValueError("写路径不能越过工程根")
    norm = "/".join(p for p in pure.parts if p not in ("", ".")).lower()
    if not norm:
        raise ValueError("写路径不能指向整个工程根")
    return norm.rstrip("/"), is_dir


def paths_conflict(a, b):
    """文件精确相同，或显式目录包含另一个路径，即冲突。"""
    pa, da = a
    pb, db = b
    if pa == pb:
        return True
    if da and pb.startswith(pa + "/"):
        return True
    if db and pa.startswith(pb + "/"):
        return True
    return False


def validate_plan(plan, delivery=False):
    errors = []
    if not isinstance(plan, dict):
        return ["根节点必须是 JSON 对象"]
    if plan.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if not isinstance(plan.get("project"), str) or not plan.get("project", "").strip():
        errors.append("project 必须是非空字符串")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks 必须是非空数组")
        return errors

    by_id = {}
    owners = {}
    for i, task in enumerate(tasks):
        label = f"tasks[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{label} 必须是对象")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not ID_RE.fullmatch(tid):
            errors.append(f"{label}.id 须匹配 {ID_RE.pattern}")
            continue
        label = tid
        if tid in by_id:
            errors.append(f"{label}: id 重复")
            continue
        by_id[tid] = task
        if not isinstance(task.get("title"), str) or not task.get("title", "").strip():
            errors.append(f"{label}: title 不能为空")
        if not isinstance(task.get("owner"), str) or not task.get("owner", "").strip():
            errors.append(f"{label}: owner 不能为空")
        readonly = task.get("readonly", False)
        if not isinstance(readonly, bool):
            errors.append(f"{label}: readonly 必须是布尔值")
            readonly = False
        writes = task.get("writes", [])
        if not isinstance(writes, list) or any(not isinstance(x, str) for x in writes):
            errors.append(f"{label}: writes 必须是字符串数组")
            writes = []
        if readonly and writes:
            errors.append(f"{label}: 只读任务不得声明 writes")
        if not readonly and not writes:
            errors.append(f"{label}: 实现任务必须声明 writes；只读任务请标 readonly=true")
        normalized = []
        for w in writes:
            try:
                normalized.append(norm_write(w))
            except ValueError as e:
                errors.append(f"{label}: {w!r}：{e}")
        owners[tid] = normalized
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or any(
            not isinstance(x, str) or not x.strip() for x in acceptance
        ):
            errors.append(f"{label}: acceptance 必须是非空字符串数组")
        deps = task.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(x, str) for x in deps):
            errors.append(f"{label}: depends_on 必须是字符串数组")
        elif len(deps) != len(set(deps)):
            errors.append(f"{label}: depends_on 有重复项")
        if tid in deps:
            errors.append(f"{label}: 不能依赖自己")
        status = task.get("status", "pending")
        if status not in STATUSES:
            errors.append(f"{label}: status 非法（可选 {sorted(STATUSES)}）")
        evidence = task.get("evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(x, str) or not x.strip() for x in evidence):
            errors.append(f"{label}: evidence 必须是非空字符串组成的数组")
        if delivery:
            if status != "completed":
                errors.append(f"{label}: 交付门禁要求 status=completed，当前为 {status}")
            if not evidence:
                errors.append(f"{label}: 交付门禁要求 evidence 非空")

    for tid, task in by_id.items():
        for dep in task.get("depends_on", []):
            if dep not in by_id:
                errors.append(f"{tid}: 依赖 {dep!r} 不存在")

    # 文件所有权：不同任务不能写同一文件，显式目录也不能互相包含。
    tids = sorted(owners)
    for i, left in enumerate(tids):
        for right in tids[i + 1:]:
            for a in owners[left]:
                for b in owners[right]:
                    if paths_conflict(a, b):
                        errors.append(f"文件所有权冲突：{left} 与 {right} 都可写 {a[0]!r} / {b[0]!r}")

    # DAG：三色 DFS；依赖缺失已单独报告。
    state = {}

    def visit(tid, stack):
        if state.get(tid) == 2:
            return
        if state.get(tid) == 1:
            errors.append("依赖环：" + " -> ".join(stack + [tid]))
            return
        state[tid] = 1
        for dep in by_id[tid].get("depends_on", []):
            if dep in by_id:
                visit(dep, stack + [tid])
        state[tid] = 2

    for tid in by_id:
        visit(tid, [])
    return errors


def load_plan(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"任务板不存在：{p}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"任务板不是合法 UTF-8 JSON：{e}") from e


def write_md(path, plan, errors, delivery):
    p = Path(path)
    if not p.parent.is_dir():
        raise ValueError(f"报告目录不存在：{p.parent}")
    lines = [f"# 多代理任务板：{plan.get('project', '未命名')}", ""]
    lines.append(f"- 模式：{'交付门禁' if delivery else '派代理前检查'}")
    lines.append(f"- 任务数：{len(plan.get('tasks', []))}")
    lines.append(f"- 结论：{'未通过' if errors else '通过'}")
    lines.append("")
    if errors:
        lines.append("## 问题")
        lines.extend(f"- {e}" for e in errors)
    else:
        lines.append("结构、依赖与文件所有权检查通过。")
        if delivery:
            lines.append("所有任务均声明 completed 且有 evidence；仍须主代理复核证据并跑整体验证。")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def selftest():
    good = template("自检")
    # 移除模板默认的目录层级差异并不掩盖冲突检查：当前三任务所有权互不重叠。
    cases = []
    cases.append(("合法任务板", good, False, False))

    bad = copy.deepcopy(good)
    bad["tasks"][1]["writes"] = ["src/main/java/com/example/mod/"]
    cases.append(("目录包含冲突", bad, False, True))

    bad = copy.deepcopy(good)
    bad["tasks"][0]["depends_on"] = ["integrate"]
    cases.append(("依赖环", bad, False, True))

    bad = copy.deepcopy(good)
    bad["tasks"][1]["depends_on"] = ["missing"]
    cases.append(("依赖悬空", bad, False, True))

    bad = copy.deepcopy(good)
    bad["tasks"][0]["writes"] = ["notes.md"]
    cases.append(("只读任务越界声明", bad, False, True))

    done = copy.deepcopy(good)
    for task in done["tasks"]:
        task["status"] = "completed"
        task["evidence"] = ["自检证据"]
    cases.append(("合法交付任务板", done, True, False))

    bad = copy.deepcopy(done)
    bad["tasks"][1]["evidence"] = []
    cases.append(("交付缺证据", bad, True, True))

    failed = 0
    for name, plan, delivery, should_fail in cases:
        errors = validate_plan(plan, delivery=delivery)
        got_fail = bool(errors)
        ok = got_fail == should_fail
        failed += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}：{'拦截' if got_fail else '通过'}")
        if not ok:
            print("    " + "；".join(errors[:3]))
    print(f"自检：{len(cases) - failed}/{len(cases)} 通过")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(
        description="多代理任务板：派代理前查分工冲突，交付前查状态与证据",
        epilog="退出码：0=通过 / 1=门禁未通过 / 2=参数、路径或 JSON 错误",
    )
    ap.add_argument("command", nargs="?", choices=["init", "check"], help="init 生成模板；check 校验任务板")
    ap.add_argument("plan", nargs="?", help="任务板 JSON 路径")
    ap.add_argument("--project", default="未命名项目", help="init 使用的项目名")
    ap.add_argument("--delivery", action="store_true", help="交付门禁：所有任务 completed 且 evidence 非空")
    ap.add_argument("--md", help="同时输出 Markdown 检查报告")
    ap.add_argument("--selftest", action="store_true", help="运行合法/非法样本双向自检")
    args = ap.parse_args()

    if args.selftest:
        if args.command or args.plan:
            ap.error("--selftest 不能与 init/check 同用")
        return selftest()
    if not args.command or not args.plan:
        ap.print_help()
        return 2

    try:
        if args.command == "init":
            if args.delivery or args.md:
                raise ValueError("init 不支持 --delivery / --md")
            p = Path(args.plan)
            if p.exists():
                raise ValueError(f"拒绝覆盖已有文件：{p}")
            if not p.parent.is_dir():
                raise ValueError(f"输出目录不存在：{p.parent}")
            data = template(args.project)
            errors = validate_plan(data)
            if errors:
                raise ValueError("内置模板未通过自检：" + "；".join(errors))
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
            print(f"✓ 已生成任务板：{p}")
            print("下一步：按实际工作包修改 writes / depends_on / acceptance，再运行 check。")
            return 0

        plan = load_plan(args.plan)
        errors = validate_plan(plan, delivery=args.delivery)
        if args.md:
            write_md(args.md, plan, errors, args.delivery)
        if errors:
            print(f"✗ {'交付门禁' if args.delivery else '任务板检查'}未通过（{len(errors)} 项）")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(f"✓ {'交付门禁' if args.delivery else '任务板检查'}通过：{len(plan['tasks'])} 个任务")
        if args.delivery:
            print("注意：这只是声明完整性检查；主代理仍须复核证据并运行整体验证。")
        return 0
    except ValueError as e:
        print(f"[错误] {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
