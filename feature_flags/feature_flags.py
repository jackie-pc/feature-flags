import copy
from fastapi import HTTPException
from typing import Optional

import reflex as rx


class FeatureFlags(rx.Model, table=True, primary_key="name"):
    name: str
    value: str


class State(rx.State):
    """The app state."""
    feature_flag_dict_as_loaded_from_db: Optional[dict[str, str]] = None

    feature_flag_dict: Optional[dict[str, str]] = {}
    pending_deletes: set[str] = set()

    new_modal_is_open: bool = False
    modal_error: Optional[str] = None

    modal_flag_name: Optional[str] = ""
    modal_flag_value: Optional[str] = ""

    @rx.var
    def save_color(self) -> str:
        if self.pending_creates or self.pending_deletes or self.pending_updates:
            return "red"
        else:
            return ""

    def load_ff_from_db(self):
        print("loading from DB")
        with rx.session() as session:
            feature_flags = session.exec(FeatureFlags.select).all()

        self.feature_flag_dict_as_loaded_from_db = {}
        for ff in feature_flags:
            self.feature_flag_dict_as_loaded_from_db[ff.name] = ff.value
        print("Loaded " + str(self.feature_flag_dict_as_loaded_from_db))

    @rx.var
    def feature_flag_name_value_pairs(self) -> list[tuple[str, str]]:
        if self.feature_flag_dict is None:
            self.feature_flag_dict = {}

        if self.feature_flag_dict_as_loaded_from_db is None:
            self.load_ff_from_db()

        return sorted([(flag_name, flag_value) for flag_name, flag_value in self.merged_ff().items()])

    def merged_ff(self) -> dict[str, str]:
        m = {**(self.feature_flag_dict_as_loaded_from_db or {}), **self.feature_flag_dict}
        for k in self.pending_deletes:
            del m[k]
        return m

    @rx.var
    def pending_creates(self) -> set[str]:
        result = set(copy.deepcopy(self.merged_ff()))
        for k in (self.feature_flag_dict_as_loaded_from_db or {}):
            if k in result:
                result.remove(k)
        for k in self.pending_deletes:
            if k in result:
                result.remove(k)
        return result

    @rx.var
    def pending_updates(self) -> set[str]:
        result = set()
        for k in self.feature_flag_dict_as_loaded_from_db or {}:
            if k in self.feature_flag_dict:
                result.add(k)
        for k in self.pending_deletes:
            if k in result:
                result.remove(k)
        return result

    def set_feature_flag(self, k: str, val: str):
        self.feature_flag_dict[k] = val
        print(self.feature_flag_dict)

    def save_to_db(self):
        desired_dict = copy.deepcopy(self.merged_ff())
        for k in self.pending_deletes:
            if k in desired_dict:
                del desired_dict[k]

        for k, v in desired_dict.items():
            with rx.session() as session:
                session.add(
                    FeatureFlags(
                        name=k, value=v
                    ),
                )
                session.commit()

        for k in self.pending_deletes:
            print("Deleting: " + k)
            with rx.session() as session:
                ff = session.exec(FeatureFlags.select.where(FeatureFlags.name == k)).first()
                if ff:
                    print("DB Deleting " + str(ff))
                    session.delete(ff)
                session.commit()
        print("Saving to DB")
        self.pending_deletes = set()
        self.feature_flag_dict = {}
        self.load_ff_from_db()

    def stage_new_modal(self):
        if self.modal_flag_name in self.merged_ff():
            self.modal_error = "Flag already exists"
            return
        else:
            self.feature_flag_dict[self.modal_flag_name] = self.modal_flag_value
        self.new_modal_is_open = False
        self.modal_flag_name = ""
        self.modal_flag_value = ""
        self.modal_error = ""
        print(self.feature_flag_dict)

    def cancel_new_modal(self):
        self.new_modal_is_open = False
        self.modal_flag_name = ""
        self.modal_flag_value = ""
        self.modal_error = ""

    def stage_delete_feature_flag(self, k: str):
        self.pending_deletes.add(k)


def index() -> rx.Component:
    return rx.fragment(
        rx.color_mode_button(rx.color_mode_icon(), float="right"),
        rx.vstack(
            rx.heading("Flex-Flags", font_size="2em"),
            rx.hstack(
                rx.button("Save", on_click=State.save_to_db, color=State.save_color),
                rx.button("Add new", on_click=lambda: State.set_new_modal_is_open(True)),
            ),
            rx.cond(State.pending_deletes, rx.text(f"Pending deletes: {State.pending_deletes}", color="red")),
            rx.cond(State.pending_creates, rx.text(f"Pending creates: {State.pending_creates}", color="red")),
            rx.cond(State.pending_updates, rx.text(f"Pending updates: {State.pending_updates}", color="red")),
            rx.modal(
                rx.modal_overlay(
                    rx.modal_content(
                        rx.modal_header("Add new feature flag"),
                        rx.modal_body(
                            "Add a new feature flag",
                            rx.hstack(
                                rx.input(value=State.modal_flag_name, width='30%', on_change=State.set_modal_flag_name),
                                rx.input(alue=State.modal_flag_value, on_change=State.set_modal_flag_value,
                                         width='70%')),
                            rx.cond(State.modal_error, rx.text(State.modal_error, color="red"))
                        ),
                        rx.modal_footer(
                            rx.button(
                                "Stage", on_click=State.stage_new_modal,
                            ),
                            rx.button(
                                "Cancel", on_click=State.cancel_new_modal,
                            )
                        ),
                    )
                ),
                is_open=State.new_modal_is_open,
            ),
            rx.table(
                rx.thead(
                    rx.tr(
                        rx.th("Flag name"),
                        rx.th("Flag value"),
                    )
                ),
                rx.tbody(
                    rx.foreach(
                        State.feature_flag_name_value_pairs,
                        lambda p: rx.tr(
                            rx.td(p[0]),
                            rx.td(
                                rx.input(value=p[1], on_change=lambda v: State.set_feature_flag(p[0], v), width="70%")),
                            rx.td(rx.button("Delete", on_click=lambda: State.stage_delete_feature_flag(p[0]),
                                            width="30%"))
                        ),
                    ),
                ),
            ),
            on_mount=State.load_ff_from_db,
        ),
    )


app = rx.App()


async def get_flag(flag_name: str):
    try:
        with rx.session() as session:
            flag = session.exec(FeatureFlags.select.where(FeatureFlags.name == flag_name)).first()
            if flag is None:
                raise ValueError()
    except Exception:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"flag_value": flag.value}


app.api.add_api_route("/flag/{flag_name}", get_flag)

app.add_page(index)
app.compile()
