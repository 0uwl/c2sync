import logging

from watchdog.observers import Observer
from watchdog.events import DirModifiedEvent, FileModifiedEvent, FileSystemEventHandler

from c2sync import Project
from c2sync.differ import Differ

LOGGER = logging.getLogger(__name__)

class ConfigWatcher(FileSystemEventHandler):
    def __init__(self, project: Project) -> None:
        self.filepath = project.EDIT_FILE
        self.differ = Differ(project)
        self.last_content = self.read_file()


    def read_file(self) -> list[str]:
        with open(self.filepath, 'r') as file:
            return file.readlines()
            
    
    def on_modified(self, event: DirModifiedEvent | FileModifiedEvent) -> None:
        if event.src_path != self.filepath:
            return

        old_content = self.last_content
        new_content = self.read_file()

        self.differ.save_to_staging(old_content, new_content)