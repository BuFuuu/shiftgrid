from factory import create_app, run
from Extensions import management

# The core alone is single-project (`create_app(single_project=True,
# lock_scope_on_create=True)`); the management extension adds multi-project
# management, the Scope page and credentials on top.
app = create_app(**management.factory_kwargs())


if __name__ == "__main__":
    management.startup_banner()
    run(app)
