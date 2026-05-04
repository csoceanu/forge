"""Jira data models for API responses."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class JiraUser:
    """Represents a Jira user."""

    account_id: str
    display_name: str
    email: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any] | None) -> "JiraUser | None":
        """Create a JiraUser from an API response field."""
        if not data:
            return None
        return cls(
            account_id=data.get("accountId", ""),
            display_name=data.get("displayName", ""),
            email=data.get("emailAddress"),
        )


@dataclass
class JiraIssue:
    """Represents a Jira issue from the REST API."""

    key: str
    id: str
    summary: str
    description: str
    status: str
    issue_type: str
    parent_key: str | None = None
    labels: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    created: datetime | None = None
    updated: datetime | None = None
    reporter: JiraUser | None = None
    assignee: JiraUser | None = None

    @property
    def project_key(self) -> str:
        """Extract project key from issue key (e.g., 'AISOS' from 'AISOS-104')."""
        return self.key.rsplit("-", 1)[0] if "-" in self.key else self.key

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "JiraIssue":
        """Create a JiraIssue from an API response.

        Args:
            data: Raw API response dictionary.

        Returns:
            Populated JiraIssue instance.
        """
        fields = data.get("fields", {})

        # Extract description text from ADF
        description = ""
        desc_field = fields.get("description")
        if desc_field and isinstance(desc_field, dict):
            description = cls._extract_text_from_adf(desc_field)
        elif isinstance(desc_field, str):
            description = desc_field

        # Extract parent key if present
        parent_key = None
        parent = fields.get("parent")
        if parent:
            parent_key = parent.get("key")

        # Parse dates
        created = None
        if fields.get("created"):
            created = datetime.fromisoformat(fields["created"].replace("Z", "+00:00"))

        updated = None
        if fields.get("updated"):
            updated = datetime.fromisoformat(fields["updated"].replace("Z", "+00:00"))

        # Collect custom fields
        custom_fields = {k: v for k, v in fields.items() if k.startswith("customfield_")}

        # Extract reporter and assignee
        reporter = JiraUser.from_api_response(fields.get("reporter"))
        assignee = JiraUser.from_api_response(fields.get("assignee"))

        return cls(
            key=data.get("key", ""),
            id=data.get("id", ""),
            summary=fields.get("summary", ""),
            description=description,
            status=fields.get("status", {}).get("name", ""),
            issue_type=fields.get("issuetype", {}).get("name", ""),
            parent_key=parent_key,
            labels=fields.get("labels", []),
            custom_fields=custom_fields,
            created=created,
            updated=updated,
            reporter=reporter,
            assignee=assignee,
        )

    @staticmethod
    def _extract_text_from_adf(adf: dict[str, Any]) -> str:
        """Extract plain text from Atlassian Document Format.

        Args:
            adf: ADF document structure.

        Returns:
            Extracted plain text.
        """
        if not isinstance(adf, dict):
            return str(adf) if adf else ""

        content = adf.get("content", [])
        texts = []

        for node in content:
            if node.get("type") == "paragraph":
                para_texts = []
                for child in node.get("content", []):
                    if child.get("type") == "text":
                        para_texts.append(child.get("text", ""))
                texts.append("".join(para_texts))
            elif node.get("type") == "text":
                texts.append(node.get("text", ""))

        return "\n\n".join(texts)


@dataclass
class JiraComment:
    """Represents a Jira comment from the REST API."""

    id: str
    body: str
    author_id: str
    author_name: str
    created: datetime | None = None
    updated: datetime | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "JiraComment":
        """Create a JiraComment from an API response.

        Args:
            data: Raw API response dictionary.

        Returns:
            Populated JiraComment instance.
        """
        # Extract body text from ADF
        body = ""
        body_field = data.get("body")
        if body_field and isinstance(body_field, dict):
            body = JiraIssue._extract_text_from_adf(body_field)
        elif isinstance(body_field, str):
            body = body_field

        author = data.get("author", {})

        # Parse dates
        created = None
        if data.get("created"):
            created = datetime.fromisoformat(data["created"].replace("Z", "+00:00"))

        updated = None
        if data.get("updated"):
            updated = datetime.fromisoformat(data["updated"].replace("Z", "+00:00"))

        return cls(
            id=data.get("id", ""),
            body=body,
            author_id=author.get("accountId", ""),
            author_name=author.get("displayName", ""),
            created=created,
            updated=updated,
        )
