import logging
import os

from ciscoconfparse2 import Diff

from c2sync import Project, git_ops

LOGGER = logging.getLogger(__name__)

# git merge-file always passes -L labels (see git_ops.merge_file), so a real
# marker line is never bare - checking for the label-bearing form avoids
# mistaking an operator's own '<<<<<<<' banner text (unlikely in IOS config,
# but not impossible) for an actual unresolved conflict.
_CONFLICT_MARKER_START = '<<<<<<< '
_CONFLICT_MARKER_END = '>>>>>>> '


class Differ:
    """
    Computes the CLI commands needed to go from the last confirmed baseline
    to the current EDIT_FILE.

    Backed by ciscoconfparse2's hier_config-based Diff, which parses IOS
    config into a real parent/child tree instead of counting indentation -
    this is what makes real deletion handling (a deleted line becomes a
    `no <command>`, not silently dropped) and multi-line blocks (banners,
    macros) work correctly, for free.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self.staging_file = project.STAGING_FILE


    @staticmethod
    def diff_lines(from_config: str, to_config: str) -> list[str]:
        """
        The CLI commands needed to turn from_config into to_config, via
        ciscoconfparse2's parent/child tree diff rather than raw text. The
        one place that actually calls ciscoconfparse2.Diff - refresh_staging
        (EDIT_FILE vs. the git baseline) and `c2sync revert` (a past commit
        vs. the device's live running-config) both build on this.
        """
        return Diff(from_config, to_config, syntax='ios').get_diff()


    @staticmethod
    def has_conflict_markers(content: str) -> bool:
        """
        True if content still has unresolved git merge conflict markers left
        by push's drift reconciliation (see MergeConflictError in main.py).

        Conflict-marker text is not valid IOS syntax, so nothing may hand it
        to diff_lines()/ciscoconfparse2 - every caller that reads EDIT_FILE
        (staging refresh, and StateEngine's host_dirty derivation) must check
        this *first* and treat a hit as "don't parse this," not merely warn
        after the fact.
        """
        return any(
            line.startswith(_CONFLICT_MARKER_START) or line.startswith(_CONFLICT_MARKER_END)
            for line in content.splitlines()
        )


    def refresh_staging(self, baseline_config: str, current_config: str) -> bool:
        """
        Recompute the staging file from scratch by diffing a known-good
        baseline against the current config, replacing whatever was staged
        before.

        This is what stands in for the watcher: instead of appending to
        staging incrementally as edits happen, we always recompute the full
        diff on demand against the last confirmed baseline, so the result
        only ever depends on the current file content - never on how many
        times it's been saved or whether anything was watching.

        Returns True if any commands were staged, False if the current
        config already matches the baseline.
        """
        lines = self.diff_lines(baseline_config, current_config)

        with open(self.staging_file, 'w') as file:
            for line in lines:
                file.write(line + '\n')

        return bool(lines)


    def refresh_staging_from_files(self) -> bool:
        """
        Same as refresh_staging, but reads the baseline from git HEAD (the
        last confirmed push) and the current config from the project's
        EDIT_FILE.
        """
        edit_file_name = os.path.relpath(self.project.EDIT_FILE, self.project.PROJECT_DIR)
        baseline = git_ops.show_at_head(self.project.PROJECT_DIR, edit_file_name) or ''

        with open(self.project.EDIT_FILE) as file:
            current = file.read()

        return self.refresh_staging(baseline, current)


    def clear_staging(self) -> None:
        """
        Clear the staging file.
        """
        # Open in write mode truncates the file to zero length
        open(self.staging_file, 'w').close()
