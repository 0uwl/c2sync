from c2sync import Project

# Project.at(), not the plain constructor: PROJECT_DIR must be an isolated
# subdirectory, not '.' (the default's implicit "cwd is already the project"
# case) - conftest.py's session fixture shutil.rmtree()s PROJECT_DIR at
# teardown, which would delete the entire working directory tests run from
# if PROJECT_DIR were '.'.
PROJECT = Project.at(
    'test-project-fixture',
    NAME='test-project',
    SERIAL_DEVICE='NOT USED',
)