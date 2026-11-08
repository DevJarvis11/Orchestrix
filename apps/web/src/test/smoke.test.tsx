import { render, screen } from "@testing-library/react";
import { StatusBadge, toneOf } from "../components/StatusBadge";
import { decodeClaims, hasRole, identityOf } from "../lib/auth";

function fakeJwt(claims: object): string {
	const enc = (o: object) =>
		btoa(JSON.stringify(o))
			.replace(/=+$/, "")
			.replace(/\+/g, "-")
			.replace(/\//g, "_");
	return `${enc({ alg: "none" })}.${enc(claims)}.sig`;
}

describe("status rendering (NFR-11)", () => {
	it("shows a text label and an icon, not just colour", () => {
		const { container } = render(<StatusBadge status="awaiting_human" />);
		expect(screen.getByText("awaiting human")).toBeInTheDocument();
		expect(container.querySelector("svg")).not.toBeNull();
	});

	it("maps statuses to the SPEC 18.2 palette", () => {
		expect(toneOf("running")).toBe("running");
		expect(toneOf("in_review")).toBe("review");
		expect(toneOf("approved")).toBe("approved");
		expect(toneOf("skipped")).toBe("skipped");
	});
});

describe("roles from the token (SPEC 18.2 route guards)", () => {
	it("reads realm roles and treats admin as every role", () => {
		const token = fakeJwt({
			sub: "u1",
			email: "a@b.c",
			realm_access: { roles: ["admin", "offline_access"] },
		});
		expect(decodeClaims(token).sub).toBe("u1");
		const id = identityOf(token);
		expect(id.roles).toEqual(["admin"]);
		expect(hasRole(id, "operator")).toBe(true);
		expect(
			hasRole(
				identityOf(fakeJwt({ realm_access: { roles: ["requester"] } })),
				"reviewer",
			),
		).toBe(false);
	});
});
