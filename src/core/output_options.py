"""The selected sections are the complete output contract for a session."""
from dataclasses import dataclass


@dataclass(frozen=True)
class OutputOptions:
    guests: bool = True
    comments: bool = True
    layout: str = "combined"

    def __post_init__(self):
        if type(self.guests) is not bool or type(self.comments) is not bool:
            raise ValueError("Section selections must be true or false.")
        if self.layout not in ("combined", "separate"):
            raise ValueError("Choose combined video or separate clips.")

    @property
    def sections(self):
        return (("comments",) if self.comments else ()) + ("feed",) + (("guests",) if self.guests else ())

    @property
    def summary(self):
        names = " + ".join(s.title() for s in self.sections)
        return f"1 video · {names}" if self.layout == "combined" else f"{len(self.sections)} clip{'s' if len(self.sections) != 1 else ''} · {names}"

    @classmethod
    def from_legacy(cls, panel=True, guests=None, comments=None, layout="combined"):
        return cls(bool(panel) if guests is None else guests,
                   bool(panel) if comments is None else comments, layout)
