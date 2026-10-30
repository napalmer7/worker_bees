"""Read-only, bounded investigation against a named persisted snapshot."""

from worker_bees.models import ContextResult, Index, QueryResult, StatusResult, Symbol


class Investigation:
    def __init__(self, index: Index):
        self.index = index

    def warnings(self) -> list[str]:
        unknown = sum(call.target_id is None for call in self.index.calls)
        messages = [
            "Static potential calls only; runtime dispatch and non-call uses are not indexed.",
            f"{unknown} unresolved call sites; zero matches does not prove absence of callers.",
            "Source is from the recorded commit, independent of current working-tree edits.",
        ]
        if self.index.snapshot.status == "partial":
            messages.append("Partial index: inspect snapshot issues before interpreting results.")
        if self.index.snapshot.excluded_paths:
            messages.append("Configured path exclusions apply; inspect snapshot excluded_paths.")
        return messages

    @staticmethod
    def check_limit(limit: int) -> None:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")

    def symbol(self, identifier: str) -> Symbol:
        matches = [
            symbol
            for symbol in self.index.symbols
            if symbol.id == identifier or symbol.qualified_name == identifier
        ]
        if len(matches) != 1:
            raise ValueError(
                "symbol not found or ambiguous; use find and select an exact symbol ID"
            )
        return matches[0]

    def find(self, query: str, limit: int = 50) -> QueryResult:
        self.check_limit(limit)
        if not query or len(query) > 512:
            raise ValueError("query must contain between 1 and 512 characters")
        matches = sorted(
            (s for s in self.index.symbols if query.casefold() in s.qualified_name.casefold()),
            key=lambda s: (s.qualified_name, s.path, s.line),
        )
        return QueryResult(
            snapshot=self.index.snapshot,
            query=query,
            items=list(matches[:limit]),
            total=len(matches),
            truncated=len(matches) > limit,
            warnings=self.warnings(),
        )

    def references(self, identifier: str, limit: int = 50) -> QueryResult:
        self.check_limit(limit)
        symbol = self.symbol(identifier)
        matches = [call for call in self.index.calls if call.target_id == symbol.id]
        return QueryResult(
            snapshot=self.index.snapshot,
            query=f"potential callers of {symbol.qualified_name}",
            items=list(matches[:limit]),
            total=len(matches),
            truncated=len(matches) > limit,
            warnings=self.warnings(),
        )

    def unresolved(self, limit: int = 50) -> QueryResult:
        self.check_limit(limit)
        matches = [call for call in self.index.calls if call.target_id is None]
        return QueryResult(
            snapshot=self.index.snapshot,
            query="unresolved calls",
            items=list(matches[:limit]),
            total=len(matches),
            truncated=len(matches) > limit,
            warnings=self.warnings(),
        )

    def context(self, identifier: str, max_lines: int = 80) -> ContextResult:
        if not 1 <= max_lines <= 200:
            raise ValueError("max_lines must be between 1 and 200")
        symbol = self.symbol(identifier)
        document = next(doc for doc in self.index.documents if doc.path == symbol.path)
        source = document.source.splitlines()[symbol.line - 1 : symbol.end_line]
        snippet = "\n".join(source[:max_lines])
        return ContextResult(
            snapshot=self.index.snapshot,
            symbol=symbol,
            source=snippet[:12_000],
            truncated=len(source) > max_lines or len(snippet) > 12_000,
            warnings=self.warnings(),
        )

    def impact(self, identifier: str, depth: int = 2, limit: int = 50) -> QueryResult:
        self.check_limit(limit)
        if not 1 <= depth <= 10:
            raise ValueError("depth must be between 1 and 10")
        symbol = self.symbol(identifier)
        seen = {symbol.id}
        frontier = {symbol.id}
        reached: list[str] = []
        for _ in range(depth):
            next_frontier = {
                call.caller_id
                for call in self.index.calls
                if call.target_id in frontier and call.caller_id not in seen
            }
            seen.update(next_frontier)
            reached.extend(sorted(next_frontier))
            frontier = next_frontier
            if not frontier:
                break
        by_id = {s.id: s for s in self.index.symbols}
        items = sorted((by_id[item] for item in reached), key=lambda s: (s.path, s.line))
        warnings = self.warnings() + [f"Reverse call traversal limited to {depth} levels."]
        return QueryResult(
            snapshot=self.index.snapshot,
            query=f"potential impact of {symbol.qualified_name}",
            items=list(items[:limit]),
            total=len(items),
            truncated=len(items) > limit,
            warnings=warnings,
        )

    def status(self) -> StatusResult:
        resolved = sum(call.target_id is not None for call in self.index.calls)
        return StatusResult(
            snapshot=self.index.snapshot,
            symbols=len(self.index.symbols),
            resolved_calls=resolved,
            unresolved_calls=len(self.index.calls) - resolved,
        )
