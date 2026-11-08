package orchestrix.tool_test

import data.orchestrix.tool

tools := {
	"web.search": {"enabled": true, "sensitive": false, "allowed_specialists": ["research"], "rate_limit_per_min": 60},
	"comms.send_email": {"enabled": true, "sensitive": true, "allowed_specialists": ["writer"], "rate_limit_per_min": 10},
	"files.write_artifact": {"enabled": true, "sensitive": false, "allowed_specialists": ["writer"], "rate_limit_per_min": 60},
	"data.sql_query": {"enabled": false, "sensitive": false, "allowed_specialists": ["data_analyst"], "rate_limit_per_min": 60},
}

org := {"email_domain": "orchestrix.local", "side_effect_tools": ["comms.send_email", "files.write_artifact"]}

base := {"tool": "web.search", "specialist": "research", "args": {}, "calls_last_minute": 0, "context_tainted": false}

decide(overrides) := d if {
	d := tool.decision with input as object.union(base, overrides)
		with data.tools as tools
		with data.org as org
}

test_allow_plain_call if {
	decide({}) == {"result": "allow", "reasons": set()}
}

test_deny_unknown_tool if {
	d := decide({"tool": "shell.exec"})
	d.result == "deny"
	"unknown tool" in d.reasons
}

test_deny_disabled_tool if {
	d := decide({"tool": "data.sql_query", "specialist": "data_analyst"})
	d.result == "deny"
	"tool disabled" in d.reasons
}

test_deny_specialist_not_allowed if {
	d := decide({"specialist": "writer"})
	"tool not allowed for specialist" in d.reasons
}

test_deny_rate_limit if {
	d := decide({"calls_last_minute": 60})
	d.result == "deny"
	"rate limit exceeded" in d.reasons
}

test_deny_wins_over_approval if {
	d := decide({"tool": "comms.send_email", "specialist": "research", "args": {"to": ["a@orchestrix.local"]}})
	d.result == "deny"
}

test_sensitive_tool_needs_approval if {
	d := decide({"tool": "comms.send_email", "specialist": "writer", "args": {"to": ["a@orchestrix.local"]}})
	d.result == "require_approval"
	"sensitive tool" in d.reasons
	not "recipient outside organisation" in d.reasons
}

test_outside_recipient_flagged if {
	d := decide({"tool": "comms.send_email", "specialist": "writer", "args": {"to": ["x@evil.example"]}})
	"recipient outside organisation" in d.reasons
}

test_tainted_side_effect_needs_approval if {
	d := decide({"tool": "files.write_artifact", "specialist": "writer", "context_tainted": true})
	d.result == "require_approval"
	"untrusted content in context" in d.reasons
}

test_tainted_read_only_tool_allowed if {
	decide({"context_tainted": true}).result == "allow"
}
