"""Pure compatibility adapter for VM evaluators that discard CDP exceptionDetails.

The caller retains its existing HTTP transport and target selection. Expressions run
exactly once in the selected page/frame; this module performs no I/O or retries.
"""
import json

# Versioned marker prevents ordinary page results from being mistaken for an envelope.
_ENVELOPE_MARKER = "higan.browser_eval.v1"


def build_eval_wrapper(js: str) -> str:
    """Catch syntax/runtime/Promise failures inside the existing page evaluator.

    Indirect eval preserves global page scope, not this wrapper's local bindings.
    Returning a JSON string avoids the server losing undefined/non-JSON CDP values.
    """
    if not isinstance(js, str) or not js.strip():
        raise ValueError("js must be a non-empty string")
    source = json.dumps(js, ensure_ascii=True)
    marker = json.dumps(_ENVELOPE_MARKER)
    return r'''(async () => {
  const marker = MARKER;
  const stringify = JSON.stringify.bind(JSON);
  const describe = (error) => {
    // Thrown values can be primitives or hostile objects with throwing getters.
    const read = (key, fallback) => {
      try { return String(error != null && error[key] != null ? error[key] : fallback).slice(0, 2000); }
      catch (_) { return fallback; }
    };
    let fallback = "Thrown value";
    try { fallback = String(error).slice(0, 2000); } catch (_) {}
    return {name: read("name", "Error"), message: read("message", fallback), stack: read("stack", "")};
  };
  let value;
  try {
    // Await catches rejected Promises; source is JSON-escaped, never interpolated as code.
    value = await (0, eval)(SOURCE);
  } catch (error) {
    return stringify({marker, ok: false, error_type: "evaluation_error", exception: describe(error)});
  }
  if (value === undefined) {
    return stringify({marker, ok: true, value_type: "undefined", value: null});
  }
  try {
    // Reject lossy values rather than silently dropping functions/undefined or NaN.
    return stringify({marker, ok: true, value_type: "json", value}, (_key, item) => {
      const kind = typeof item;
      if (kind === "undefined" || kind === "function" || kind === "symbol" || kind === "bigint" ||
          (kind === "number" && !Number.isFinite(item))) {
        throw new TypeError("Evaluation result is not JSON-safe: " + kind);
      }
      return item;
    });
  } catch (error) {
    return stringify({marker, ok: false, error_type: "serialization_error", exception: describe(error)});
  }
})()'''.replace("MARKER", marker, 1).replace("SOURCE", source, 1)


def decode_eval_response(response: dict) -> dict:
    """Decode only validated wrappers, preserving transport failures and metadata.

    Missing envelopes are explicit protocol errors, never a successful null value;
    the caller must not retry because the expression may already have side effects.
    """
    if not isinstance(response, dict):
        return {"ok": False, "error_type": "evaluation_protocol_error", "error": "VM evaluation response is not an object"}
    # Do not disguise a server/HTTP-level failure as a JavaScript exception.
    if response.get("ok") is not True:
        return dict(response)
    result = dict(response)
    raw = result.pop("value", None)
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        envelope = None
    if not isinstance(envelope, dict) or envelope.get("marker") != _ENVELOPE_MARKER:
        result.update(ok=False, error_type="evaluation_protocol_error", error="VM evaluation returned no valid diagnostic envelope")
        return result
    if envelope.get("ok") is True and envelope.get("value_type") in ("json", "undefined") and "value" in envelope:
        # Python None alone cannot distinguish JavaScript null from undefined.
        result.update(ok=True, value=envelope["value"], value_type=envelope["value_type"])
        return result
    if envelope.get("ok") is False and envelope.get("error_type") in ("evaluation_error", "serialization_error") and isinstance(envelope.get("exception"), dict):
        result.update(ok=False, error_type=envelope["error_type"], error="Browser evaluation failed" if envelope["error_type"] == "evaluation_error" else "Browser result is not JSON-safe", exception=envelope["exception"])
        return result
    result.update(ok=False, error_type="evaluation_protocol_error", error="VM evaluation returned an invalid diagnostic envelope")
    return result
