from app.services.tasks import (
    cancel_task,
    complete_task,
    create_task_from_message,
    delete_task,
    list_open_tasks,
    reschedule_task,
    skip_task,
)

UPDATE_ID = 999999901


def main() -> None:
    row = create_task_from_message(
        title="SMOKE test task",
        description="smoke",
        category="other",
        estimated_duration_minutes=1,
        duration_source="estimate",
        telegram_message_id=0,
        telegram_update_id=UPDATE_ID,
    )
    print("create:", row)
    dup = create_task_from_message(
        title="SMOKE test task",
        description="smoke",
        category="other",
        estimated_duration_minutes=1,
        duration_source="estimate",
        telegram_message_id=0,
        telegram_update_id=UPDATE_ID,
    )
    print("dedup expected None:", dup)
    short_id = row[0]
    print("in queue:", any(r[0] == short_id for r in list_open_tasks()))
    print("skip:", skip_task(short_id))
    print("reschedule:", reschedule_task(short_id))
    print("complete:", complete_task(short_id))
    print("complete again expected invalid_state:", complete_task(short_id))
    print("cancel completed expected invalid_state:", cancel_task(short_id))
    print("delete:", delete_task(short_id))
    print("delete again expected not_found:", delete_task(short_id))


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
