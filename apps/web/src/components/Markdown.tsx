// Agent-produced Markdown: react-markdown + rehype-sanitize; raw HTML never reaches the DOM (18.2).
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

export function Markdown({ children }: { children: string }) {
	return (
		<div className="prose-agent text-sm">
			<ReactMarkdown
				rehypePlugins={[rehypeSanitize]}
				components={{
					a: ({ node: _node, ...props }) => (
						<a {...props} target="_blank" rel="noopener noreferrer nofollow" />
					),
				}}
			>
				{children}
			</ReactMarkdown>
		</div>
	);
}
