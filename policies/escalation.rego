# Who may decide an escalation (SPEC 15.1, 2): reviewers and admins of the assigned team, never
# on their own task (four-eyes rule); clarify questions are answered by the requester only.
# input: {actor: {id, roles, team_ids}, escalation: {level, assignee_team_id}, task: {requester_id}}
package orchestrix.escalation

default allow := false

allow if {
	input.escalation.level != "clarify"
	some role in ["reviewer", "admin"]
	role in input.actor.roles
	input.escalation.assignee_team_id in input.actor.team_ids
	input.actor.id != input.task.requester_id
}

allow if {
	input.escalation.level == "clarify"
	input.actor.id == input.task.requester_id
}
