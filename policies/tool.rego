# Tool-call policy (SPEC 15.1, FR-90). Evaluated before every specialist tool call.
# input: {tool, specialist, args, user, team, calls_last_minute, context_tainted}
# data.tools: the tool registry, pushed by the worker and the admin API (PUT /v1/data/tools).
# data.org:   {email_domain, side_effect_tools}
package orchestrix.tool

deny contains "unknown tool" if not data.tools[input.tool]

deny contains "tool disabled" if {
	data.tools[input.tool]
	not data.tools[input.tool].enabled
}

deny contains "tool not allowed for specialist" if {
	data.tools[input.tool]
	not input.specialist in data.tools[input.tool].allowed_specialists
}

deny contains "rate limit exceeded" if {
	input.calls_last_minute >= data.tools[input.tool].rate_limit_per_min
}

approval contains "sensitive tool" if data.tools[input.tool].sensitive

approval contains "recipient outside organisation" if {
	input.tool == "comms.send_email"
	some r in input.args.to
	not endswith(lower(r), lower(data.org.email_domain))
}

approval contains "untrusted content in context" if {
	input.context_tainted
	input.tool in data.org.side_effect_tools
}

decision := {"result": "deny", "reasons": deny} if count(deny) > 0

decision := {"result": "require_approval", "reasons": approval} if {
	count(deny) == 0
	count(approval) > 0
}

decision := {"result": "allow", "reasons": set()} if {
	count(deny) == 0
	count(approval) == 0
}
