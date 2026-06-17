from factory import create_app, run

app = create_app(
    single_project=True,
    lock_scope_on_create=True,
)


if __name__ == "__main__":
    run(app)
