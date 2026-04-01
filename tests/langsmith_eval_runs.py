"""langsmith_eval_runs 模块。"""

import argparse
import math
from typing import Any, Iterable, Optional

from langsmith import Client
from langsmith.evaluation import StringEvaluator


def _to_text(value: Any) -> str:
    """将任意运行输入/输出对象转换为可评分文本。

    优先提取常见字段 `output`、`answer`，其余对象回退为 `str(...)`。

    Args:
        value: 原始对象，可为 None、字符串、字典或其他类型。

    Returns:
        str: 归一化后的文本。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "output" in value:
            return str(value.get("output") or "")
        if "answer" in value:
            return str(value.get("answer") or "")
        return str(value)
    return str(value)


def _char_jaccard(a: str, b: str) -> float:
    """计算字符级 Jaccard 相似度。

    Args:
        a: 文本 A。
        b: 文本 B。

    Returns:
        float: 相似度分数，范围 [0, 1]。
    """
    a_set = set(str(a or "").strip().lower())
    b_set = set(str(b or "").strip().lower())
    if not a_set and not b_set:
        return 1.0
    union = a_set.union(b_set)
    if not union:
        return 0.0
    inter = a_set.intersection(b_set)
    return len(inter) / len(union)


def _contains_low_confidence_phrase(text: str) -> bool:
    """判断文本是否包含低置信度/拒答措辞。

    Args:
        text: 待检查文本。

    Returns:
        bool: 命中低置信度短语时返回 True。
    """
    t = str(text or "")
    low_conf_phrases = [
        "无法回答",
        "无法确定",
        "不清楚",
        "抱歉",
        "稍后重试",
        "出现异常",
    ]
    return any(phrase in t for phrase in low_conf_phrases)


def _score_without_reference(run_input: str, run_output: str) -> float:
    """在无参考答案时计算启发式分数。

    分数由输出长度、输入输出重叠度和低置信短语惩罚项组成。

    Args:
        run_input: 用户输入文本。
        run_output: 模型输出文本。

    Returns:
        float: 归一化分数，范围 [0, 1]。
    """
    output = str(run_output or "").strip()
    if not output:
        return 0.0
    base = min(1.0, max(0.2, len(output) / 260.0))
    overlap = _char_jaccard(run_input, run_output)
    penalty = 0.35 if _contains_low_confidence_phrase(output) else 0.0
    score = (0.55 * base) + (0.45 * overlap) - penalty
    return max(0.0, min(1.0, score))


def _label_by_score(score: float) -> str:
    """将数值分数映射为离散标签。 

    Args:
        score: 数值分数。

    Returns:
        str: GOOD/OK/WEAK/BAD 之一。
    """
    if score >= 0.8:
        return "GOOD"
    if score >= 0.55:
        return "OK"
    if score >= 0.3:
        return "WEAK"
    return "BAD"


def _grader(run_input: str, run_output: str, answer: Optional[str]) -> dict[str, Any]:
    """LangSmith 字符串评估回调。

    若提供参考答案则走相似度评分；否则使用无参考启发式评分。

    Args:
        run_input: 运行输入文本。
        run_output: 运行输出文本。
        answer: 可选参考答案。

    Returns:
        dict[str, Any]: 评估结果，包含 score、value、comment。
    """
    if answer is not None and str(answer).strip():
        score = _char_jaccard(run_output, answer)
    else:
        score = _score_without_reference(run_input, run_output)
    score = max(0.0, min(1.0, float(score)))
    return {
        "score": score,
        "value": _label_by_score(score),
        "comment": f"auto-score={score:.4f}",
    }


def _iter_top_runs(client: Client, project_name: str, limit: int) -> Iterable[Any]:
    """迭代项目中的顶层成功运行记录。

    Args:
        client: LangSmith 客户端。
        project_name: 项目名称。
        limit: 最多拉取运行数量。

    Returns:
        Iterable[Any]: 可迭代运行对象序列。
    """
    return client.list_runs(
        project_name=project_name,
        execution_order=1,
        error=False,
        limit=limit,
    )


def evaluate_project_runs(project_name: str, limit: int) -> dict[str, Any]:
    """对指定项目的运行记录执行自动评估并汇总。

    Args:
        project_name: LangSmith 项目名称。
        limit: 评估的最大运行数量。

    Returns:
        dict[str, Any]: 汇总结果，包含样本数与平均分。
    """
    client = Client()
    evaluator = StringEvaluator(evaluation_name="auto_qa_health", grading_function=_grader)

    evaluated = 0
    score_sum = 0.0

    for run in _iter_top_runs(client, project_name, limit):
        client.evaluate_run(run, evaluator)
        run_input = _to_text(getattr(run, "inputs", {}))
        run_output = _to_text(getattr(run, "outputs", {}))
        score = _score_without_reference(run_input, run_output)
        score_sum += score
        evaluated += 1

    avg_score = (score_sum / evaluated) if evaluated else 0.0
    return {
        "project": project_name,
        "evaluated_runs": evaluated,
        "avg_score": round(avg_score, 4),
    }


def main() -> None:
    """命令行入口：解析参数并输出评估摘要。

    Returns:
        None: 无返回值。
    """
    parser = argparse.ArgumentParser(description="Evaluate LangSmith runs for this project")
    parser.add_argument("--project", required=True, help="LangSmith project name")
    parser.add_argument("--limit", type=int, default=100, help="Max top-level runs to evaluate")
    args = parser.parse_args()

    result = evaluate_project_runs(project_name=args.project, limit=max(1, int(args.limit)))
    print("langsmith evaluation summary")
    print(f"project={result['project']}")
    print(f"evaluated_runs={result['evaluated_runs']}")
    print(f"avg_score={result['avg_score']}")


if __name__ == "__main__":
    main()

