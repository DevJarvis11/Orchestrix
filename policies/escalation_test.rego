package orchestrix.escalation_test

import data.orchestrix.escalation

reviewer := {"id": "u-bob", "roles": ["requester", "reviewer"], "team_ids": ["t-platform"]}

esc(level) := {"level": level, "assignee_team_id": "t-platform"}

test_team_reviewer_may_decide if {
	escalation.allow with input as {"actor": reviewer, "escalation": esc("approve_action"), "task": {"requester_id": "u-alice"}}
}

test_admin_of_team_may_decide if {
	escalation.allow with input as {"actor": {"id": "u-ada", "roles": ["admin"], "team_ids": ["t-platform"]}, "escalation": esc("approve_plan"), "task": {"requester_id": "u-alice"}}
}

test_four_eyes_rule if {
	not escalation.allow with input as {"actor": reviewer, "escalation": esc("approve_action"), "task": {"requester_id": "u-bob"}}
}

test_other_team_denied if {
	not escalation.allow with input as {"actor": {"id": "u-carol", "roles": ["reviewer"], "team_ids": ["t-research"]}, "escalation": esc("approve_action"), "task": {"requester_id": "u-alice"}}
}

test_operator_cannot_approve if {
	not escalation.allow with input as {"actor": {"id": "u-oscar", "roles": ["operator"], "team_ids": ["t-platform"]}, "escalation": esc("approve_action"), "task": {"requester_id": "u-alice"}}
}

test_requester_cannot_approve_actions if {
	not escalation.allow with input as {"actor": {"id": "u-dave", "roles": ["requester"], "team_ids": ["t-platform"]}, "escalation": esc("take_over"), "task": {"requester_id": "u-alice"}}
}

test_clarify_answered_by_requester_only if {
	escalation.allow with input as {"actor": {"id": "u-alice", "roles": ["requester"], "team_ids": []}, "escalation": esc("clarify"), "task": {"requester_id": "u-alice"}}
	not escalation.allow with input as {"actor": reviewer, "escalation": esc("clarify"), "task": {"requester_id": "u-alice"}}
}
