import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Tabs } from "../components/ui";
import { parseMode, resolveDark } from "../lib/theme";
import { rejectReason } from "../lib/uploads";
import { greeting, titleFromPrompt } from "../lib/utils";

describe("theme", () => {
	it("treats anything but light or dark as follow-the-system", () => {
		expect(parseMode("light")).toBe("light");
		expect(parseMode("dark")).toBe("dark");
		expect(parseMode(null)).toBe("system");
		expect(parseMode("purple")).toBe("system");
	});

	it("resolves the effective theme from the mode and the OS setting", () => {
		expect(resolveDark("dark", false)).toBe(true);
		expect(resolveDark("light", true)).toBe(false);
		expect(resolveDark("system", true)).toBe(true);
		expect(resolveDark("system", false)).toBe(false);
	});
});

describe("ask box helpers", () => {
	it("titles a task from the first non-empty line", () => {
		expect(titleFromPrompt("\n  Compare   vector DBs \nand more")).toBe(
			"Compare vector DBs",
		);
	});

	it("cuts long titles at a word boundary within the API limit", () => {
		const title = titleFromPrompt(
			"Research the top five open-source vector databases and compare their licensing, benchmarks and community health in depth",
		);
		expect(title.length).toBeLessThanOrEqual(80);
		expect(title.endsWith("…")).toBe(true);
		expect(title).not.toMatch(/\s…$/);
	});

	it("greets by time of day", () => {
		expect(greeting(9)).toBe("Good morning");
		expect(greeting(14)).toBe("Good afternoon");
		expect(greeting(20)).toBe("Good evening");
	});

	it("refuses files the API would reject (FR-02)", () => {
		expect(rejectReason("notes.md", 10)).toBeNull();
		expect(rejectReason("REPORT.PDF", 10)).toBeNull();
		expect(rejectReason("run.exe", 10)).toBe("type not allowed");
		expect(rejectReason("big.csv", 11 * 1024 * 1024)).toBe("over 10 MB");
	});
});

function Harness() {
	const [v, setV] = useState<"a" | "b" | "c">("a");
	return (
		<Tabs
			label="Pick"
			value={v}
			onChange={setV}
			options={[
				{ value: "a", label: "Alpha" },
				{ value: "b", label: "Beta" },
				{ value: "c", label: "Gamma" },
			]}
		/>
	);
}

describe("segmented control", () => {
	it("selects on click and moves with the arrow keys", () => {
		render(<Harness />);
		const alpha = screen.getByRole("tab", { name: "Alpha" });
		expect(alpha).toHaveAttribute("aria-selected", "true");
		fireEvent.click(screen.getByRole("tab", { name: "Gamma" }));
		expect(screen.getByRole("tab", { name: "Gamma" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
		fireEvent.keyDown(screen.getByRole("tab", { name: "Gamma" }), {
			key: "ArrowRight",
		});
		expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute(
			"aria-selected",
			"true",
		);
	});
});
