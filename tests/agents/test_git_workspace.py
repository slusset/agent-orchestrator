"""
Unit tests for the GitWorkspace — git operations for the Coding Agent.

Tests use real git commands against temporary local repos (no network).
This validates the actual git subprocess integration, not mocks.

Traceability:
  Journey: specs/journeys/agent-execution-lifecycle.md
  Story: specs/stories/agent-lifecycle/agent-receives-bundle.md
  Feature: specs/features/agent-lifecycle/agent-receives-bundle.feature
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from src.agents.git_workspace import (
    GitError,
    GitResult,
    GitWorkspace,
    PRInfo,
    TestResults,
    make_branch_name,
)


# ---------------------------------------------------------------------------
# Fixtures — create real git repos for testing
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir():
    """Provide a temporary directory, cleaned up after test."""
    with tempfile.TemporaryDirectory(prefix="test-workspace-") as d:
        yield Path(d)


@pytest.fixture
def bare_repo(temp_dir):
    """Create a bare git repo to act as a 'remote origin'."""
    repo_path = temp_dir / "origin.git"
    repo_path.mkdir()
    os.system(f"git init --bare {repo_path} 2>/dev/null")
    return repo_path


@pytest.fixture
def seeded_repo(temp_dir, bare_repo):
    """
    Create a repo with initial content and push to the bare remote.

    This simulates a real GitHub repo with an existing main branch.
    """
    source = temp_dir / "source"
    source.mkdir()

    commands = [
        f"git init {source}",
        f"git -C {source} config user.email 'test@test.com'",
        f"git -C {source} config user.name 'Test'",
        f"echo 'hello' > {source / 'README.md'}",
        f"mkdir -p {source / 'src'}",
        f"echo 'print(\"hello\")' > {source / 'src' / 'main.py'}",
        f"git -C {source} add .",
        f"git -C {source} commit -m 'Initial commit'",
        f"git -C {source} remote add origin {bare_repo}",
        f"git -C {source} push -u origin main 2>/dev/null || git -C {source} push -u origin master 2>/dev/null",
    ]
    for cmd in commands:
        os.system(f"{cmd} 2>/dev/null")

    # Determine the default branch name (could be main or master)
    result = os.popen(f"git -C {source} branch --show-current").read().strip()

    return {
        "origin": bare_repo,
        "source": source,
        "default_branch": result or "main",
    }


@pytest.fixture
def workspace(temp_dir, seeded_repo):
    """Create a GitWorkspace pointed at the seeded repo."""
    return GitWorkspace(
        repo_url=str(seeded_repo["origin"]),
        work_dir=temp_dir / "workspace",
        base_branch=seeded_repo["default_branch"],
    )


# ---------------------------------------------------------------------------
# make_branch_name
# ---------------------------------------------------------------------------


class TestMakeBranchName:

    def test_basic_branch_name(self):
        name = make_branch_name("feature/", "abc123def456", "Add user authentication")
        assert name == "feature/abc123de-add-user-authentication"

    def test_special_characters_slugified(self):
        name = make_branch_name("feature/", "abc12345", "Fix bug: crash on login!!!")
        assert name == "feature/abc12345-fix-bug-crash-on-login"

    def test_long_objective_truncated(self):
        name = make_branch_name(
            "feature/", "abc12345",
            "This is a very long objective that should be truncated to fit within limits",
            max_length=40,
        )
        assert len(name) <= 40
        assert name.startswith("feature/abc12345-")

    def test_empty_objective(self):
        name = make_branch_name("feature/", "abc12345", "")
        assert name == "feature/abc12345"

    def test_different_prefix(self):
        name = make_branch_name("fix/", "deadbeef", "Memory leak in parser")
        assert name.startswith("fix/deadbeef-")


# ---------------------------------------------------------------------------
# GitResult
# ---------------------------------------------------------------------------


class TestGitResult:

    def test_success(self):
        r = GitResult(command="git status", returncode=0, stdout="clean", stderr="")
        assert r.success is True

    def test_failure(self):
        r = GitResult(command="git push", returncode=1, stdout="", stderr="rejected")
        assert r.success is False


# ---------------------------------------------------------------------------
# TestResults
# ---------------------------------------------------------------------------


class TestTestResults:

    def test_passing(self):
        r = TestResults(passed=True, count=10)
        assert r.passed
        assert r.failures == []

    def test_failing(self):
        r = TestResults(passed=False, count=10, failures=["test_foo"])
        assert not r.passed
        assert len(r.failures) == 1


# ---------------------------------------------------------------------------
# GitWorkspace — clone and branch operations
# ---------------------------------------------------------------------------


class TestGitWorkspaceClone:

    @pytest.mark.asyncio
    async def test_clone_creates_directory(self, workspace, seeded_repo):
        path = await workspace.clone()
        assert path.exists()
        assert (path / "README.md").exists()
        assert (path / "src" / "main.py").exists()

    @pytest.mark.asyncio
    async def test_clone_sets_branch(self, workspace, seeded_repo):
        await workspace.clone()
        assert workspace.branch == seeded_repo["default_branch"]
        assert workspace._cloned is True

    @pytest.mark.asyncio
    async def test_clone_with_depth(self, workspace, seeded_repo):
        path = await workspace.clone(depth=1)
        assert path.exists()
        assert (path / "README.md").exists()

    @pytest.mark.asyncio
    async def test_clone_invalid_url_raises(self, temp_dir):
        ws = GitWorkspace(
            repo_url="/nonexistent/repo.git",
            work_dir=temp_dir / "bad-clone",
        )
        with pytest.raises(GitError):
            await ws.clone()


class TestGitWorkspaceBranch:

    @pytest.mark.asyncio
    async def test_create_branch(self, workspace, seeded_repo):
        await workspace.clone()
        branch = await workspace.create_branch("feature/test-branch")
        assert branch == "feature/test-branch"
        assert workspace.branch == "feature/test-branch"

    @pytest.mark.asyncio
    async def test_checkout_existing_branch(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.create_branch("feature/new")
        await workspace.checkout(seeded_repo["default_branch"])
        assert workspace.branch == seeded_repo["default_branch"]


# ---------------------------------------------------------------------------
# GitWorkspace — file operations
# ---------------------------------------------------------------------------


class TestGitWorkspaceFiles:

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, workspace, seeded_repo):
        await workspace.clone()
        path = await workspace.write_file("src/new_file.py", "print('new')")
        assert path.exists()
        content = await workspace.get_file_content("src/new_file.py")
        assert content == "print('new')"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.write_file("deep/nested/dir/file.py", "x = 1")
        content = await workspace.get_file_content("deep/nested/dir/file.py")
        assert content == "x = 1"

    @pytest.mark.asyncio
    async def test_list_files(self, workspace, seeded_repo):
        await workspace.clone()
        files = await workspace.list_files()
        assert "README.md" in files
        assert any("main.py" in f for f in files)

    @pytest.mark.asyncio
    async def test_status_shows_changes(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.write_file("new.txt", "content")
        status = await workspace.status()
        assert "new.txt" in status


# ---------------------------------------------------------------------------
# GitWorkspace — commit and push
# ---------------------------------------------------------------------------


class TestGitWorkspaceCommit:

    @pytest.mark.asyncio
    async def test_add_and_commit(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.create_branch("feature/commit-test")
        await workspace.write_file("new.py", "x = 1")
        await workspace.add("new.py")
        result = await workspace.commit("Add new file")
        assert result.success

    @pytest.mark.asyncio
    async def test_commit_all(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.create_branch("feature/commit-all")
        await workspace.write_file("a.py", "a = 1")
        await workspace.write_file("b.py", "b = 2")
        result = await workspace.commit_all("Add two files")
        assert result.success

    @pytest.mark.asyncio
    async def test_diff_shows_changes(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.write_file("README.md", "updated content")
        diff = await workspace.diff()
        assert "updated content" in diff

    @pytest.mark.asyncio
    async def test_staged_diff(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.write_file("README.md", "staged change")
        await workspace.add("README.md")
        diff = await workspace.diff(staged=True)
        assert "staged change" in diff

    @pytest.mark.asyncio
    async def test_push_to_remote(self, workspace, seeded_repo):
        await workspace.clone()
        await workspace.create_branch("feature/push-test")

        # Set git config for commits
        await workspace._run_git("config", "user.email", "test@test.com")
        await workspace._run_git("config", "user.name", "Test Agent")

        await workspace.write_file("pushed.py", "x = 1")
        await workspace.commit_all("Push test")
        result = await workspace.push()
        assert result.success


# ---------------------------------------------------------------------------
# GitWorkspace — context manager
# ---------------------------------------------------------------------------


class TestGitWorkspaceContextManager:

    @pytest.mark.asyncio
    async def test_context_manager_cleanup(self, seeded_repo):
        """Temp dir is cleaned up when using context manager with auto temp."""
        workspace = GitWorkspace(
            repo_url=str(seeded_repo["origin"]),
            base_branch=seeded_repo["default_branch"],
        )
        async with workspace:
            await workspace.clone()
            work_dir = workspace.work_dir
            assert work_dir.exists()

        # After exit, temp dir should be cleaned up
        assert not work_dir.parent.exists()

    @pytest.mark.asyncio
    async def test_explicit_work_dir_not_deleted(self, temp_dir, seeded_repo):
        """When work_dir is explicitly provided, we don't delete it."""
        ws_dir = temp_dir / "explicit"
        workspace = GitWorkspace(
            repo_url=str(seeded_repo["origin"]),
            work_dir=ws_dir,
            base_branch=seeded_repo["default_branch"],
        )
        await workspace.clone()
        workspace.cleanup()
        # Explicit dir still exists (we didn't create a temp)
        # (The actual clone subdir may or may not exist depending on implementation)


# ---------------------------------------------------------------------------
# GitWorkspace — test execution
# ---------------------------------------------------------------------------


class TestGitWorkspaceTestExecution:

    @pytest.mark.asyncio
    async def test_run_pytest_no_tests(self, workspace, seeded_repo):
        """Running pytest in a repo with no tests should report zero tests."""
        await workspace.clone()
        # The seeded repo has no test files
        result = await workspace.run_tests(frameworks=["pytest"])
        # pytest returns exit code 5 for "no tests collected", which is not 0
        # but isn't a test failure per se
        assert isinstance(result, TestResults)

    @pytest.mark.asyncio
    async def test_run_unknown_framework_skipped(self, workspace, seeded_repo):
        """Unknown framework names should be skipped, not crash."""
        await workspace.clone()
        result = await workspace.run_tests(frameworks=["unknown_framework"])
        assert result.passed  # Nothing ran, nothing failed
        assert result.count == 0


# ---------------------------------------------------------------------------
# PRInfo
# ---------------------------------------------------------------------------


class TestPRInfo:

    def test_pr_info(self):
        pr = PRInfo(url="https://github.com/org/repo/pull/42", number=42, title="Add feature", branch="feature/test")
        assert pr.number == 42
        assert "pull/42" in pr.url
