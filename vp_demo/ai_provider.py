"""DeepSeek boundary. No SDK dependency, arbitrary endpoints, tools or code execution."""
import difflib
import hashlib
import http.client
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MODEL = os.environ.get("VPLAB_AI_MODEL", "deepseek-flash")
PROMPT_VERSION = "review-4-two-stage"
SCHEMA_VERSION = 1
ANALYSIS_MAX_TOKENS = 3000
REVIEW_MAX_TOKENS = 6000
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


class ProviderError(ValueError):
    pass


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
ANALYZE_PROMPT = (PROMPT_DIR / "review-analyze.txt").read_text(encoding="utf-8").strip()
DISTILL_PROMPT = (PROMPT_DIR / "review-distill.txt").read_text(encoding="utf-8").strip()

# Keep the tested reasoning instructions, adapting only the production input/output contract.
INPUT_CONTRACT = """
【应用输入约定】
source含题面、编号源码versions、程序生成的diffs、选定转换steps及范围facts。
源码每行前缀是行号，externalId为提交编号；行号前缀不是源码的一部分。
facts区分源码份数与修改步数。没有题面时不要臆造题意；历史覆盖未知不等于缺少题面。
question是用户本次关注的问题；relatedExperiences仅作参考，不能当作已验证根因。
mode=hint时给检查方向与局部提示，不透露完整解法；mode=review时可解释完整推理。
只有一份源码时不讨论前后修复；没有AC时只作阶段分析，不虚构最终成功。
"""
JSON_CONTRACT = """
【正式页面输出约定，替代上面的自然语言排版要求】
只输出一个JSON对象，不要Markdown围栏。以字段承载同一张经验卡内容，不另输出审稿过程。
summary用1～2句概括主要发现。steps按source.steps的顺序和数量逐一简述修改，不能遗漏、合并或新增；没有转换时为[]。
每步observation写实际改动，hypothesis写简短原因或具体疑点，test为可选的自检建议；无必要建议时填空字符串，不强行编反例或期望答案。
candidates最多1条；没有可靠发现可为[]。title是短标题，incident写本次改动与原因，scenario写适用情况，rule写经验，checklist写1～2项下次检查。
category只能是错误复盘、解题思路、实现技巧、调试方法、复杂度判断。tags最多8项。
步骤和候选尽量各引用1～2段最相关源码：submissionId必须来自versions.externalId，lineStart/lineEnd为该版本行号，quote逐字复制对应源码行（去掉行号前缀），保留缩进。
无法找到对应源码时evidence可为空，不能编造摘录；不能输出verified、testExecuted等校验标志。
公共限制集中写limitations，最多3条，不在各字段反复重复。未执行测试不等于没有题面，不声称本次推理已验证。
结构如下（示例值必须换成真实内容）：
{"summary":"主要发现","steps":[{"before":"10","after":"11","observation":"实际修改","hypothesis":"原因解释","test":"","evidence":[{"submissionId":"11","lineStart":1,"lineEnd":1,"quote":"int main() {}"}]}],"candidates":[{"title":"经验标题","category":"实现技巧","scenario":"适用场景","rule":"下次做法","incident":"本次改动和原因","checklist":["检查动作"],"tags":["边界"],"evidence":[{"submissionId":"11","lineStart":1,"lineEnd":1,"quote":"int main() {}"}]}],"limitations":[]}
"""
ANALYZE_SYSTEM_PROMPT = ANALYZE_PROMPT + INPUT_CONTRACT
SYSTEM_PROMPT = DISTILL_PROMPT + INPUT_CONTRACT + JSON_CONTRACT


def build_review_input(snapshot):
    versions = []
    sources = {}
    for record in snapshot["records"]:
        # CRLF and LF describe identical source lines; keep originals in snapshots.
        lines = record["source"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
        sources[record["externalId"]] = "\n".join(lines)
        versions.append({k: record[k] for k in ("externalId", "verdict", "language", "participantType", "submittedAt")} |
                        {"source": "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))})
    count, transitions = len(versions), len(snapshot["steps"])
    diffs = [{"before": step["before"], "after": step["after"],
              "diff": "\n".join(difflib.unified_diff(
                  sources[step["before"]].splitlines(),
                  sources[step["after"]].splitlines(),
                  fromfile=step["before"], tofile=step["after"], lineterm=""))}
             for step in snapshot["steps"]]
    accepted = next((r["externalId"] for r in snapshot["records"] if r["verdict"] == "OK"), None)
    return {"facts": {"hasProblemStatement": bool(snapshot["problem"].get("text", "").strip()),
                      "sourceVersionCount": count, "transitionCount": transitions,
                      "observedSubmissionCount": len(snapshot.get("timeline", versions)),
                      "missingSourceCount": len(snapshot.get("missingSourceIds", [])),
                      "analysisKind": "single_snapshot" if count <= 1 else "pair" if count == 2 else "journey",
                      "testsExecuted": False},
            "scope": snapshot["analysisScope"], "coverage": snapshot["coverageState"],
            "limitations": snapshot["warnings"], "mode": snapshot["mode"],
            "problem": snapshot["problem"], "question": snapshot["question"],
            "steps": snapshot["steps"], "versions": versions, "diffs": diffs,
            "acceptedSubmissionId": accepted,
            "relatedExperiences": snapshot["relatedExperiences"]}


class DeepSeekProvider:
    name = "deepseek"
    model = MODEL

    def generation_config(self):
        return {"thinking": {"type": "disabled"}, "temperature": 0.2, "max_tokens": REVIEW_MAX_TOKENS}

    def workflow_config(self):
        return {"type": "pure_ai_two_stage", "callCount": 2,
                "analysisMaxTokens": ANALYSIS_MAX_TOKENS,
                "promptHashes": {"analyze": hashlib.sha256(ANALYZE_SYSTEM_PROMPT.encode()).hexdigest(),
                                 "distill": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()}}

    def analyze(self, snapshot, key, on_stage=None):
        source = build_review_input(snapshot)
        generation = dict(snapshot.get("generation", self.generation_config()))

        def stage(name, prompt, inputs, config, structured=False):
            # Cancellation is checked immediately before each outbound call.
            if on_stage is not None and on_stage(name) is False:
                raise ProviderError("分析已取消，未发出下一轮请求。")
            try:
                return self._request(prompt, inputs, key, config, structured,
                                     model=snapshot.get("model", self.model))
            except ProviderError as exc:
                label = "题意与代码分析" if name == "analyze" else "经验提炼"
                raise ProviderError(f"{label}失败：{exc}") from None

        draft, first_usage, first_model = stage(
            "analyze", ANALYZE_SYSTEM_PROMPT, {"source": source},
            generation | {"max_tokens": min(ANALYSIS_MAX_TOKENS, generation.get("max_tokens", REVIEW_MAX_TOKENS))})
        if len(draft) > 20000:
            raise ProviderError("第一轮分析过长，未发出第二轮请求，请缩小分析范围。")
        content, final_usage, final_model = stage(
            "distill", SYSTEM_PROMPT, {"source": source, "analysisDraft": draft}, generation, True)
        try:
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            raise ProviderError("经验提炼未返回有效 JSON 对象，未保存候选经验。请手动重试。") from None
        usages = (first_usage, final_usage)
        # A missing stage usage must not be presented as a complete two-call total.
        usage = {name: sum(u[name] for u in usages)
                 for name in USAGE_FIELDS
                 if all(u is not None and name in u for u in usages)}
        usage.update(callCount=2, complete=all(u is not None and "total_tokens" in u for u in usages),
                     stages=[{"stage": "analyze", "model": first_model, "usage": first_usage},
                             {"stage": "distill", "model": final_model, "usage": final_usage}])
        return result, usage, final_model

    def _request(self, prompt, inputs, key, generation, structured=False, model=None):
        payload = {"model": model or self.model, **generation, "stream": False,
                   "messages": [{"role": "system", "content": prompt},
                                {"role": "user", "content": json.dumps(inputs, ensure_ascii=False)}]}
        if structured:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request("https://api.deepseek.com/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode(),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "VPLab/0.3"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ProviderError("模型响应过大，请缩小分析范围。")
                body = json.loads(raw)
        except urllib.error.HTTPError as exc:
            messages = {401: "DeepSeek 密钥无效，请重新配置。", 402: "DeepSeek 余额不足。",
                        429: "DeepSeek 请求过于频繁，请稍后手动重试。", 403: "DeepSeek 拒绝访问，请检查账号权限。"}
            raise ProviderError(messages.get(exc.code, f"DeepSeek 返回 HTTP {exc.code}，请稍后手动重试。")) from None
        except ProviderError:
            raise
        except json.JSONDecodeError:
            raise ProviderError("DeepSeek 响应不是有效 JSON，可能是网关返回了异常内容；本地数据已保留，请手动重试。") from None
        except TimeoutError:
            raise ProviderError("等待 DeepSeek 响应超时（TimeoutError）；本地数据已保留，请手动重试。") from None
        except urllib.error.URLError as exc:
            raise ProviderError(f"连接 DeepSeek 失败（{type(exc.reason).__name__}）；请检查网络后手动重试。") from None
        except (http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
            raise ProviderError(f"DeepSeek 响应传输中断（{type(exc).__name__}）；未接受不完整结果，请手动重试。") from None
        except Exception as exc:
            raise ProviderError(f"DeepSeek 请求或响应读取失败（{type(exc).__name__}）；本地数据已保留，请手动重试。") from None
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ProviderError("模型思考与回答达到输出预算上限（length），未保存不完整结果。请缩小范围或调整思考等级后手动重试。")
            if choice.get("finish_reason") != "stop":
                raise ProviderError("模型响应未正常结束（非 stop），未保存候选经验。请检查分析范围后手动重试。")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError("模型未返回有效正文，未保存候选经验。请手动重试。")
            usage = body.get("usage")
            safe_usage = {k: v for k, v in (usage or {}).items() if k in USAGE_FIELDS and type(v) is int and v >= 0}
            model = body.get("model") if isinstance(body.get("model"), str) else self.model
            return content, safe_usage or None, model[:100]
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("模型没有返回完整的 JSON 结果，未保存候选经验。请手动重试。") from None
