# -*- coding: utf-8 -*-
"""Coordination Graphics — EasyBIM BIM Management. (V2)

Sets up a clash-coordination look in one or more selected Plan/Section views
(batch mode — Step 1 of the wizard, see LinkPickerDialog): identifies the
Architecture, Structure (and optional Traffic) Revit links (remembered choice
> auto-detect by keyword > manual WPF pick), applies a shared view template
for the host clutter/annotation clean-up, hides every other link, deep-scans
the Arch/Struct links' Walls/Columns/Structural Framing/Foundations for
concrete-material types, and colors those types red (Structure) / blue
(Architecture) via two View Filters — driven by Settings.json (see
"Coordination Settings" and lib/easybim/coordination_settings.py). Every
other visible Model category (Stairs, Doors, Furniture, Plumbing Fixtures,
etc. — see _apply_coordination_categories) gets a plain gray line-color
override, so only the red/blue concrete coloring stands out.

SECTIONS (ViewType.Section specifically — see _is_section_view) get a
SEPARATE template (SECTION_TEMPLATE_NAME) with three deliberate differences
from Plans, confirmed directly with the user — full reasoning lives on the
SECTION_TEMPLATE_NAME constant itself: Floors stay visible, Traffic is never
shown at all (treated as just another hidden "other" link), and
Architecture's CONCRETE elements are left with zero override so they render
with their own native Material appearance instead of this tool's blue —
non-concrete elements on both sides still get the same white/gray fallback
Plans get, via two new per-link Type-Name filters instead of the blanket
category override Plans use (which can't tell Architecture's elements apart
from Structure's at all). A batch run can freely mix Plan and Section views;
run()'s per-view loop branches on _is_section_view for all of this.

Engine: IronPython 2.7 (no "#! python3" shebang) — matches the tab's other
WPF+Transaction buttons.

WHY VIEW FILTERS INSTEAD OF PER-LINK GRAPHIC OVERRIDES (read before changing
Step 8's approach): V1 of this tool used View.SetElementOverrides per element
collected from each link's own document — undocumented for elements outside
the view's own document, and RevitLinkGraphicsSettings (the "official" link
graphics API) doesn't expose per-category control even on Revit 2024+ (this
extension targets 2023+). View Filters sidestep this entirely: a
ParameterFilterElement rule matching "Type Name" is evaluated against ANY
element the view can see, host or linked, as long as the link's display is
"By Host View" (the default) — which is standard, well-documented Revit
behavior, not a workaround. The trade-off: a filter can't distinguish WHICH
link an element came from, only its own parameter values — hence the deep
Type-Name scan per link and two separate filters (Structure vs Architecture).

SHARED TYPE NAME ACROSS DISCIPLINES — MOSTLY SOLVED via Type IfcGUID: if
both links happen to use a type with the exact same Type Name (e.g. a round
column called "Ø70" in both the Architecture and Structure files), a plain
"Type Name equals" rule matches BOTH links' elements, and whichever filter
is AddFilter'd to the template first wins for all of them (confirmed live —
Structure's filter is added before Architecture's in run(), so Structure
"won" and an Architecture "Ø70" column rendered red instead of blue).
FIXED for this case by disambiguating via BuiltInParameter.IFC_TYPE_GUID
("Type IfcGUID" in Type Properties) instead of Type Name, for any name
found in BOTH arch_names and struct_names only — confirmed via reflecting
the installed RevitAPI.dll that IFC_TYPE_GUID is a real BuiltInParameter,
and confirmed EMPIRICALLY (2026-08-30, this exact "Ø70" case) that its
value differs between two identically-named types in two different linked
documents, because Revit derives it from the type's own persistent
identity, not from anything a modeler can freely choose (unlike Type Name).
See _collect_concrete_type_names' guids_by_name param and run()'s
ambiguous_names handling. Residual limitation, disclosed not fixed: if a
type's Type IfcGUID is ever blank in one of the links (never observed, but
not provably impossible), that name is excluded from BOTH filters for that
link with an explicit warning naming it, rather than guessing — the only
real fix in that case is renaming the type distinctly in one of the two
source files.

WHY MOST CATEGORY VISIBILITY LIVES ON THE TEMPLATE, NOT THE VIEW: the View
Template "include/exclude" list groups ALL model categories under one toggle
and ALL annotation categories under another (there is no per-category
granularity) — so Step 3's "host clutter" hides and Step 6's "Floors/Topo/
Dimensions/etc." hides are the same toggle and have to live in the same
place. They're set on the template. Individual RevitLinkInstance visibility
(Step 5) and halftone (Step 7) are element-level view overrides, which are
NEVER template-controlled regardless — those stay on the active view.

WHY "V/G OVERRIDES RVT LINKS" IS NOW KEPT *NOT* TEMPLATE-CONTROLLED (a
second reversal, 2026-08-30 — read the whole history, it matters): the
ORIGINAL design excluded this row from template control (added its
parameter Id to NonControlledTemplateParameterIds), reasoned to be
harmless since link visibility/halftone (Steps 5 & 7) don't depend on
this template row. A later round found that when the row is NOT
template-controlled, _apply_link_display_settings' Traffic call — which
targeted `template`, not `view`, at the time — set a value that never
propagated to any real view using the template, including a sheet's
duplicated view. That round REVERSED it: actively ENSURED the row was
template-CONTROLLED instead. That fix then caused something much worse,
found via live testing: once this row was template-controlled, Step 5's
own view-level HideElements calls for OTHER (non-selected) links stopped
sticking through to commit — a FINAL CHECK added right before commit
caught almost every non-selected link reverting to NOT-hidden by the end
of the same transaction, despite Step 5's own immediate self-check
passing. Moving Step 5 to run LAST (after every template write) fixed
THAT, but then a SELECTED link (Architecture, never in Step 5's own
target list) started getting hidden too — proof the row being
template-controlled and per-view HideElements on links simply do not
coexist safely, regardless of ordering. Fixed at the root instead:
_ensure_rvt_links_not_template_controlled now ENSURES the row is
EXCLUDED again (the ORIGINAL behavior), and _apply_link_display_settings'
Traffic call now targets `view` directly instead of `template` — solving
the original sheet-divergence concern a different way: Duplicate
(WithDetailing), used for the sheet's copy (_get_view_for_sheet), already
carries view-specific overrides (both HideElements AND per-link display
settings) onto the duplicate on its own, so no template inheritance is
needed for this at all. Autodesk's own API docs still note this row had
"No API access (Revit 2020)" and a direct
BuiltInParameter.VIS_GRAPHICS_RVT_LINKS lookup still returns null on the
versions checked, so it's still located by parameter *name*, wrapped so a
failure only adds a warning either way.

WHY GRID COLORING USES PER-ELEMENT OVERRIDES, NOT A THIRD VIEW FILTER (V3):
Walls/Columns/Framing/Foundations can be told apart per-link by Type Name
(concrete keywords), which is why those use View Filters. Grids have no
comparable per-link distinguishing parameter — a rule-based filter can only
say "this is a Grid", not "this Grid came from the Structure link", so a
single Grids rule added to both the Structure and Architecture filters would
match every grid in both and the two colors couldn't be told apart. Grid
coloring instead reuses the same best-effort View.SetElementOverrides-per-
linked-element technique already used elsewhere here for the DWG-import and
Traffic-link hides — undocumented for cross-document elements, but grids are
few enough per model that this is low-risk even if unsupported (falls back
to a warning, not a crash). Host grids are hidden the same way — element-
level, not category-level — because hiding OST_Grids as a category (the
template) would also hide the links' grids, which must stay visible.

SMART LINKED VIEW (elevation-matched view picker, one per role) — FINAL
BEHAVIOR, after several rounds of live-testing findings: each of
Architecture/Structure/Traffic gets an optional WPF combo, populated by
finding the SINGLE closest Level in the selected link(s) within
LEVEL_ELEVATION_TOLERANCE_FT of the active view's own Level (compared via
RevitLinkInstance.GetTotalTransform(), not raw Level.Elevation-to-
Level.Elevation — see _find_matching_link_views), then collecting that
level's non-template, non-dependent ViewPlans, sorted non-Coarse-detail
first, then '#'-prefixed, then genuine Floor Plans, then alphabetical.

TRAFFIC: flip-flopped twice now — settled, with an explicit condition.
Originally ByLinkView + LinkedViewId, reasoned safe because Traffic's own
coloring is host-view-only halftone (_set_traffic_halftone) plus element-
level HideElements (_restrict_traffic_link_visibility), both independent
of link display mode. Then reversed to always-Custom/never-LinkedViewId
after persistent "dimensions still visible" reports, since a link in
ByLinkView mode displays using THAT VIEW's own settings entirely,
including its own dimensions/annotations, bypassing both the host
template's category visibility and this tool's own element-level hides.
Then reversed BACK to ByLinkView + LinkedViewId (current state), on
explicit confirmation from the user that the traffic/civil consultant's
own file has a dedicated view with EVERYTHING non-Parking hidden —
model AND annotation categories both, not model-only. Given that,
ByLinkView pointed at that view is more reliable than this tool guessing
categories from the host side, not a regression. THE CONDITION THAT
MAKES THIS SAFE: the picked view must genuinely have every non-Parking
category hidden, annotations included — if it's a partially-prepared view
(model categories cleaned up but dimensions/text left on default), this
reopens the exact "dimensions bleed through" bug this flip-flop started
with. This is why Architecture/Structure are NOT symmetric with Traffic
here and never get ByLinkView regardless of how clean a picked view might
be — they need the HOST's own red/blue View Filters to keep applying,
which ByLinkView always replaces with the picked view's own filters, and
there's no "trust the consultant's view" escape hatch for that the way
there is for Traffic's much simpler halftone+hide requirement.

ARCHITECTURE/STRUCTURE: a chosen view is deliberately NEVER applied to
the link (_apply_smart_linked_view no longer calls
_apply_link_display_settings at all for these roles) — confirmed, not
just suspected: RevitLinkGraphicsSettings.LinkVisibilityType=Custom does
NOT accept a real LinkedViewId at all; a live test hit the exact
exception "The LinkedViewId of linkDisplaySettings has incorrect value
for the specified LinkVisibilityType." The only LinkVisibilityType that
DOES accept one is ByLinkView, which (per Revit's own UI docs for the
same "Linked view" picker) makes the link display using THAT view's own
filters/overrides instead of the host's — i.e. it would silently break
this tool's red/blue View Filter coloring on that link, the one thing
this whole tool exists to do. Asked the user directly which tradeoff to
take; the answer, applied here: always keep Filters correct, never chase
the linked view's exact cut plane for Architecture/Structure. The chosen
view still has real value — it identifies the right level/view for
reference and its Detail Level still gets checked (see next paragraph) —
it's just never pushed into RevitLinkGraphicsSettings for these two
roles. Both links stay on ByHostView, the default, regardless of what's
picked.

Prior, now-corrected history in this same spot: a previous version looked
up getattr(DB, "LinkVisibilityType", None) as a top-level enum type — it
isn't, that name only exists as a *property* on RevitLinkGraphicsSettings;
the real enum type is DB.LinkVisibility (ByHostView/ByLinkView/Custom).
That bug silently no-opped SetLinkOverrides on every run before it was
found via reflection.

COARSE SCALE FILL PATTERN (separate issue, same neighborhood): at Coarse
effective detail level, Revit shows a linked element's own hardcoded
"Coarse Scale Fill Pattern" Type Property (e.g. a wall type literally set
to solid blue fill) INSTEAD of respecting this tool's View Filter hatch —
root-caused by the user from a screenshot of a wall type's Type
Properties. DEAD END for changing a linked view's own Detail Level from a
host-side script — two independent, fully confirmed attempts: (1) a
Transaction directly against the linked document (a separate Document
from the host) — live testing hit a hard, unconditional Revit exception:
"Document is a linked file. Transactions can only be used in primary
documents." (2) RevitLinkGraphicsSettings.DetailLevel/.SetDetailLevel —
reflected the installed RevitAPI.dll four times across four rounds (the
fourth by actually instantiating the class and calling it live, not just
reading a member list); that class has exactly three members,
IsValidObject/LinkVisibilityType/LinkedViewId, nothing else, ever. THE
ACTUAL FIX, found one property class over: OverrideGraphicSettings (a
completely different, much older class — since Revit 2014, not gated to
2024+) ALSO has SetDetailLevel(ViewDetailLevel) — and this is the
override object already applied to the Structure/Architecture filters
via View.SetFilterOverrides (see apply_filter_to_target). Calling it
forces elements matched by that filter to render as if the detail level
were Fine, independent of the link's or the linked view's actual detail
level, entirely on the host side. Wired into build_colored_override.
_ensure_linked_view_detail_level's warning (naming the exact Coarse
linked view) and _find_matching_link_views/_populate_linked_view_combo's
Coarse-last sorting/labeling are left in place as belt-and-suspenders —
harmless now, and still correct if this override is ever unavailable.
"""

__title__ = "Coordination\nGraphics"
__author__ = "EasyBIM"
__doc__ = "Color-code concrete Walls/Columns/Framing/Foundations in the Architecture and Structure links via View Filters."

import clr
import datetime
import re
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System')

from Autodesk.Revit import DB
from Autodesk.Revit.DB import Structure
from Autodesk.Revit.UI import TaskDialog

import System
import System.Collections.Generic as SCG
import System.Windows.Controls as WC
import System.Windows.Media as WM

from System.Windows.Markup import XamlReader
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import forms, revit, script
from easybim import coordination_settings as cfgmod
from easybim import coordination_settings_ui as settings_ui
from easybim import ui as ebui

doc    = revit.doc
uidoc  = revit.uidoc
logger = script.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_NAME = u"EB_ARC/STR_CO_1-100"
# Renamed 2026-08-31, by explicit request. Kept here (not deleted) so
# ensure_view_template can find and RENAME a template already assigned to
# views from before the rename, instead of leaving it orphaned and
# silently creating a second, empty template under the new name — every
# view already using the old-named template keeps working unchanged.
_OLD_TEMPLATE_NAMES = (u"Coordination - Arch vs Str",)

FILTER_NAME_STRUCT = u"EasyBIM - Structure Concrete"
FILTER_NAME_ARCH   = u"EasyBIM - Architecture Concrete"

# Manual per-Type-Name HIDE filter (Settings.json's ManualHideTypeNames) —
# built the SAME safe way as FILTER_NAME_STRUCT/ARCH (a real "Type Name
# equals X" rule via build_or_update_type_name_filter, scoped to every
# Model category), applied via View.SetFilterVisibility instead of
# SetFilterOverrides. NOT the same thing as the crash-causing "no-rule"
# whitelist filters from earlier this project (_STALE_WHITELIST_FILTER_
# NAMES/_cleanup_stale_whitelist_filters) — those crashed specifically
# because they had NO ElementFilter rule at all; this always has a real
# rule, exactly like the two filters above, which have never crashed.
FILTER_NAME_MANUAL_HIDE = u"EasyBIM - Manual Hide"

# ── SECTIONS (added 2026-08-31, revised same day by explicit follow-up) ─────
# A dedicated second template/filter set for Section views specifically
# (ViewType.Section, not Elevation/Detail — see _is_section_view), almost
# identical to the plan template but with three deliberate differences,
# confirmed directly with the user:
#   1. Floors stay VISIBLE (removed from the hide list — see
#      SECTION_LINK_MODEL_HIDE_CATEGORY_NAMES vs LINK_MODEL_HIDE_CATEGORY_
#      NAMES). Stairs needed no change — OST_Stairs was never in any hide
#      list to begin with, in plans OR sections.
#   2. Traffic is never shown at all, regardless of whether a Traffic link
#      is picked in the wizard — treated exactly like any other
#      non-selected "other" link (fully hidden by Step 5), not given the
#      Parking-only restriction plans get. See run()'s per-view chosen_ids.
#   3. Structure's concrete hatch is UNCHANGED (still FILTER_NAME_STRUCT,
#      red hatch; non-concrete Structure content still gets the same
#      white/gray fallback plans get, via FILTER_NAME_STRUCT_FALLBACK
#      below — a category override can't tell Architecture's elements
#      apart from Structure's at all, the same "Filters can discriminate
#      by link, categories can't" limitation FILTER_NAME_STRUCT/ARCH exist
#      to work around in the first place, so the blanket category
#      fallback plans use is skipped for Sections — apply_fallback=False
#      in ensure_view_template/_apply_coordination_categories — and this
#      Type-Name filter takes over that job for Structure specifically).
#
#      ARCHITECTURE gets a COMPLETELY DIFFERENT treatment in Sections,
#      REVISED from an earlier same-day version that left it with no
#      override at all (native Material rendering) — the user then asked
#      for actual coloring after all: solid (not hatch) blue fill,
#      Halftone, AND 70% Surface Transparency, applied to EVERY relevant
#      category uniformly — concrete or not, PLUS Floors and Stairs too
#      (SECTION_ARCH_CATEGORY_NAMES = OVERRIDE_CATEGORY_NAMES + Floors +
#      Stairs) — there's no concrete/non-concrete distinction for
#      Architecture in Sections at all any more, unlike Structure or Plans.
#      One filter (FILTER_NAME_ARCH_SECTION) built from ALL of
#      Architecture's own Type Names in that wider category set (see
#      _collect_all_type_names — a plain scan, no concrete classification
#      needed since every one of these types gets the identical
#      treatment), with build_section_arch_override's OGS.
#
#      IMPORTANT CAVEAT, confirmed earlier this exact project (see
#      build_colored_override's docstring): OverrideGraphicSettings.
#      SetSurfaceTransparency only has a VISIBLE effect in Shaded/
#      Realistic visual styles — it does nothing visible in Hidden Line,
#      the default (and by far the most common) style for a 2D
#      coordination view. Still set here regardless (harmless either way,
#      and correct if the section's Visual Style is ever Shaded), but if
#      transparency doesn't visibly show, check the section view's Visual
#      Style before assuming this is broken — Halftone alone still applies
#      regardless of Visual Style and gives a similar de-emphasized look.
SECTION_TEMPLATE_NAME = u"EB_ARC/STR_CO_SECTIONS"
FILTER_NAME_STRUCT_FALLBACK = u"EasyBIM - Structure Non-Concrete (Sections)"
FILTER_NAME_ARCH_SECTION    = u"EasyBIM - Architecture (Sections)"
SECTION_ARCH_TRANSPARENCY = 70

# Column-specific dashed-line treatment (and its separate per-role filters,
# FILTER_NAME_STRUCT_COLUMNS/FILTER_NAME_ARCH_COLUMNS) was tried and then
# explicitly reversed per live-testing feedback — ALL boundary lines must be
# solid. Back to one filter per role covering all 5 categories uniformly.
# _cleanup_stale_column_filters() below removes any leftover column-specific
# filter a previous run may have created in the project.
OVERRIDE_CATEGORY_NAMES = [
    "OST_Walls",
    "OST_Columns",
    "OST_StructuralColumns",
    "OST_StructuralFoundation",
    "OST_StructuralFraming",
]

# Architecture's category scope in Sections specifically — wider than
# OVERRIDE_CATEGORY_NAMES by explicit request (Floors and Stairs included
# under the same solid-blue/halftone/transparent treatment). See the
# SECTIONS constants block above for the full reasoning.
SECTION_ARCH_CATEGORY_NAMES = OVERRIDE_CATEGORY_NAMES + ["OST_Floors", "OST_Stairs"]

GRAY_COLOR = DB.Color(190, 190, 190)
WHITE_COLOR = DB.Color(255, 255, 255)

# Host-model categories hidden on the template to declutter (Step 3). Not
# exhaustive — edit freely.
#
# BUG FOUND while implementing the whitelist filters below: OST_Parking was
# in this list from the very first version of this tool -- a CATEGORY-level
# hide on the template suppresses that category everywhere the template
# governs, including inside the Traffic link (element-level "not hidden" in
# _restrict_traffic_link_visibility cannot override a category that's
# hidden outright -- category hide is the stronger, overriding mechanism).
# This directly contradicted TRAFFIC_KEEP_CATEGORY_NAMES = ["OST_Parking"]
# the whole time, regardless of how correct the Traffic-specific logic was.
# Removed here so Parking can actually show inside the Traffic link; if the
# HOST document itself has its own Parking elements that are genuinely
# clutter, that would need a narrower, element-level hide (like
# _hide_host_grids does for Grids), not a category-level one.
HOST_CLUTTER_CATEGORY_NAMES = [
    "OST_Furniture",
    "OST_FurnitureSystems",
    "OST_Casework",
    "OST_SpecialityEquipment",
    "OST_Planting",
    "OST_Entourage",
    "OST_Site",
    "OST_Sections",
    "OST_ElevationMarks",
    "OST_Callouts",
    "OST_GenericModel",
    "OST_Mass",
]

# ── Bulletproof View-Filter-based category hides (this round) ──────────────
# Category-level SetCategoryHidden and RevitLinkGraphicsSettings both hit
# real limitations for hiding link content by category (see module
# docstring / _apply_link_display_settings). View Filters reach linked
# elements the same way this tool's Structure/Architecture coloring
# filters already do (Step 8's whole premise), so the same mechanism is
# reused here as a "hide everything except this whitelist" sweep, per
# explicit request and accepted tradeoff: this affects the ENTIRE host
# project everywhere the shared template is used, not just links.
FILTER_NAME_HIDE_ANNOTATIONS = u"EasyBIM - Hide All Annotations"
FILTER_NAME_WHITELIST_MODEL  = u"EasyBIM - Whitelist Model Categories"

# Room Tags are the one Annotation category deliberately exempted from the
# "hide all" sweep — existing, explicit spec requirement, see
# _ensure_room_tags_visible.
ANNOTATION_HIDE_KEEP_VISIBLE = ["OST_RoomTags"]

# Model categories exempted from the "hide everything else" sweep.
# Deliberately differs from the literal list requested in two ways, both
# to avoid silently reversing existing, previously-confirmed decisions
# that a fresh generic whitelist didn't cross-reference:
#   - OST_Floors is NOT included: hiding Floors inside the links has been
#     an explicit, unreversed requirement since the very first version of
#     this tool (see LINK_MODEL_HIDE_CATEGORY_NAMES) -- keeping it hidden.
#   - OST_GenericModel is NOT included: already deliberately hidden as
#     host clutter since the very first version (HOST_CLUTTER_CATEGORY_
#     NAMES above), with no complaint about it being hidden since --
#     leaving it hidden rather than silently un-hiding it.
# OST_Grids is ADDED (not in the literal request): Grids must stay
# visible per an existing, explicit requirement (Refinement 1) -- without
# this, the whitelist sweep would hide them.
WHITELIST_MODEL_CATEGORY_NAMES = [
    "OST_Walls",
    "OST_Columns",
    "OST_StructuralColumns",
    "OST_StructuralFraming",
    "OST_StructuralFoundation",
    "OST_Stairs",
    "OST_Doors",
    "OST_Windows",
    "OST_Parking",
    "OST_Roofs",
    "OST_Ceilings",
    "OST_RvtLinks",
    "OST_Grids",
]

# Model categories hidden on the template (Step 6). OST_Toposolid only exists
# from the 2024 API onward — resolved defensively at runtime.
LINK_MODEL_HIDE_CATEGORY_NAMES = [
    "OST_Floors",
    "OST_Topography",
    "OST_Toposolid",
]

# Section template variant of the list above — Floors stay VISIBLE in
# Sections (see the SECTIONS block's docstring), everything else the same.
SECTION_LINK_MODEL_HIDE_CATEGORY_NAMES = [
    "OST_Topography",
    "OST_Toposolid",
]

WALL_NONCORE_CATEGORY_NAME = "OST_WallNonCoreLayer"

# Annotation categories hidden on the template (Step 6). Room Tags are
# deliberately NOT here — kept visible explicitly, see _ensure_room_tags_visible.
LINK_ANNOTATION_HIDE_CATEGORY_NAMES = [
    "OST_Dimensions",
    "OST_ReferenceLines",
    "OST_CLines",              # "Reference Planes" — historical BuiltInCategory name
    "OST_ReferenceViewer",
    "OST_PlanRegion",
    "OST_VolumeOfInterest",    # Scope Boxes
]

# Categories left visible inside the Traffic link when enabled (Step 7).
# Narrowed to Parking only, per explicit request ("isolate Parking, keep
# the coordination view clean") — this SUPERSEDES an earlier live-testing
# compromise that also kept GenericModel/TextNotes/Dimensions/
# GenericAnnotation (because the traffic/civil model marked up slopes and
# grading via those rather than native Spot Elevation/Spot Slope
# elements). If that markup is needed again later, re-add the categories
# here — this is a plain "keep" list, not a per-category API limitation.
#
# NOT implemented via RevitLinkGraphicsSettings.SetCategoryHidden, which
# was suggested but does not exist: reflecting the installed RevitAPI.dll
# (same class already confirmed to have no DetailLevel — see
# _apply_link_display_settings/_ensure_linked_view_detail_level) shows
# RevitLinkGraphicsSettings has exactly three members, none of them
# category-related. This "keep" list instead drives the existing element-
# level hide in _restrict_traffic_link_visibility, which already
# implements exactly this "show only these categories, hide everything
# else in the link" behavior via View.HideElements.
TRAFFIC_KEEP_CATEGORY_NAMES = [
    "OST_Parking",
]

BASEMENT_TOKENS = (u"BASEMENT", u"מרתף")
DWG_KEEP_TOKEN   = u"TR"

GRID_CATEGORY_NAME = "OST_Grids"

# Refinement 2 — automatic sheet creation
TITLEBLOCK_TOKEN   = u"EB_Title Block_A0"
SHEET_NUMBER_PREFIX= u"ARC/STR-"


def _elem_name(elem):
    """Safe Element/ElementType .Name getter. Plain attribute access on
    ElementType.Name (FamilySymbol, WallType, and other *Type classes) raises
    AttributeError in this Revit API binding, on both IronPython and CPython3
    pyrevit engines (see pyrevitlabs/pyRevit#854) — Element.Name.GetValue(elem)
    is the confirmed fix, tried first. SYMBOL_NAME_PARAM is a second-tier
    fallback (mainly for FamilySymbol) in case GetValue itself is ever
    unavailable in some Revit version; None on total failure rather than a
    raised exception, so a caller iterating many elements never crashes on
    one bad one."""
    if elem is None:
        return u""
    try:
        return elem.Name or u""
    except AttributeError:
        pass
    except Exception:
        return u""

    try:
        v = DB.Element.Name.GetValue(elem)
        if v:
            return v
    except Exception:
        pass

    try:
        bip = getattr(DB.BuiltInParameter, "SYMBOL_NAME_PARAM", None)
        if bip is not None:
            p = elem.get_Parameter(bip)
            if p is not None:
                return p.AsString() or u""
    except Exception:
        pass

    return u""


def _resolve_categories(names, warnings):
    """BuiltInCategory names -> list of enum values, skipping (and warning
    about) any name that doesn't exist in the loaded RevitAPI.dll instead of
    crashing the whole command on one unfamiliar/version-gated category."""
    out = []
    for name in names:
        bic = getattr(DB.BuiltInCategory, name, None)
        if bic is None:
            warnings.append(u"Category '{}' does not exist in this Revit version — skipped.".format(name))
            continue
        out.append(bic)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# WPF LINK PICKER — 4-STEP WIZARD  (matches the Solution Section design system:
# navy/cyan gradient header, light-cyan info banner, numbered step indicator,
# card-style sections, footer "Step X of N" + Cancel/Back/Next-Apply)
# ─────────────────────────────────────────────────────────────────────────────

PICKER_XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="ARC/STR Coordination"
  Width="540" Height="640"
  MinWidth="480" MinHeight="500"
  WindowStartupLocation="CenterScreen"
  ResizeMode="CanResizeWithGrip"
  WindowStyle="SingleBorderWindow"
  FontFamily="Segoe UI"
  Background="#f7f8ff">
  <Window.Resources>

    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background"      Value="#1e248c"/>
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="18,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" CornerRadius="6"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#44b8d3"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#6b7280"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="14,0"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush"     Value="#e1e8ed"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="6" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#f0f2ff"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background"      Value="White"/>
      <Setter Property="BorderBrush"     Value="#E1E8ED"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="8"/>
      <Setter Property="Padding"         Value="16,14"/>
      <Setter Property="Margin"          Value="0,0,0,14"/>
    </Style>

    <Style x:Key="SectionLabel" TargetType="TextBlock">
      <Setter Property="FontSize"   Value="10.5"/>
      <Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Foreground" Value="#9aa0ac"/>
      <Setter Property="Margin"     Value="0,0,0,6"/>
    </Style>

    <Style x:Key="ComboStyle" TargetType="ComboBox">
      <Setter Property="Height"   Value="32"/>
      <Setter Property="FontSize" Value="12.5"/>
    </Style>

  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="76"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- HEADER -->
    <Border Grid.Row="0">
      <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
          <GradientStop Color="#1e248c" Offset="0"/>
          <GradientStop Color="#2b5cbf" Offset="0.55"/>
          <GradientStop Color="#44b8d3" Offset="1"/>
        </LinearGradientBrush>
      </Border.Background>
      <Grid Margin="20,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Border Grid.Column="0" Width="44" Height="44" CornerRadius="10" VerticalAlignment="Center"
                Background="#151b6e">
          <Grid Width="22" Height="22">
            <Border BorderBrush="White" BorderThickness="1.5" CornerRadius="2"
                    Width="14" Height="14" HorizontalAlignment="Left" VerticalAlignment="Bottom"/>
            <Border BorderBrush="White" BorderThickness="1.5" CornerRadius="2"
                    Width="14" Height="14" HorizontalAlignment="Right" VerticalAlignment="Top"/>
          </Grid>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="ARC/STR Coordination" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Set up clash-coordination graphics for the active view"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <StackPanel Grid.Column="2" Orientation="Horizontal" VerticalAlignment="Center">
          <Button x:Name="BtnGear" Content="&#9881;" Width="28" Height="28"
                  Background="Transparent" Foreground="White" BorderThickness="0"
                  FontSize="15" Cursor="Hand" Margin="0,0,4,0" ToolTip="Settings (keywords, colors, patterns)"/>
          <Button x:Name="BtnCloseX" Content="&#10005;" Width="28" Height="28"
                  Background="Transparent" Foreground="White" BorderThickness="0"
                  FontSize="13" Cursor="Hand"/>
        </StackPanel>
      </Grid>
    </Border>

    <!-- INFO BANNER -->
    <Border Grid.Row="1" Background="#ecf8fc" BorderBrush="#bbe5f0" BorderThickness="0,0,0,1" Padding="20,10">
      <StackPanel Orientation="Horizontal">
        <Border Width="18" Height="18" CornerRadius="9" Background="#44b8d3" VerticalAlignment="Top" Margin="0,1,10,0">
          <TextBlock Text="i" Foreground="White" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="InfoText" FontSize="11.5" Foreground="#1c6478" TextWrapping="Wrap"
                   VerticalAlignment="Center" Width="460"/>
      </StackPanel>
    </Border>

    <!-- STEP INDICATOR -->
    <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,16,0,10">
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge1" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum1" Text="1" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Active View" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector1" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge2" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum2" Text="2" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Select Links" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector2" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge3" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum3" Text="3" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Options" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector3" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge4" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum4" Text="4" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Summary" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
    </StackPanel>

    <!-- BODY -->
    <ScrollViewer Grid.Row="3" VerticalScrollBarVisibility="Auto" Padding="20,6,20,6">
      <Grid>

        <!-- STEP 1: SELECT VIEWS (batch mode) -->
        <StackPanel x:Name="Step1Panel">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <Grid>
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="VIEWS TO PROCESS (SELECT ONE OR MORE)"
                           Style="{StaticResource SectionLabel}" VerticalAlignment="Center"/>
                <StackPanel Grid.Column="1" Orientation="Horizontal">
                  <TextBlock x:Name="ViewSelectAll" Text="Select All" FontSize="10"
                             Foreground="#1e248c" Cursor="Hand" TextDecorations="Underline" Margin="0,0,10,0"/>
                  <TextBlock x:Name="ViewSelectNone" Text="Clear" FontSize="10"
                             Foreground="#6b7280" Cursor="Hand" TextDecorations="Underline"/>
                </StackPanel>
              </Grid>
              <TextBlock TextWrapping="Wrap" FontSize="10.5" Foreground="#8b93a7" Margin="0,4,0,8"
                         Text="The active view is pre-checked. Every checked view is set up with the SAME Architecture/Structure/Traffic links chosen in the next step."/>
              <ScrollViewer MaxHeight="280" VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="ViewsPanel"/>
              </ScrollViewer>
            </StackPanel>
          </Border>
          <TextBlock x:Name="Step1Error" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        </StackPanel>

        <!-- STEP 2: SELECT LINKS -->
        <StackPanel x:Name="Step2Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <Grid>
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="ARCHITECTURE LINKS (SELECT ONE OR MORE)"
                           Style="{StaticResource SectionLabel}" VerticalAlignment="Center"/>
                <StackPanel Grid.Column="1" Orientation="Horizontal">
                  <TextBlock x:Name="ArchSelectAll" Text="Select All" FontSize="10"
                             Foreground="#1e248c" Cursor="Hand" TextDecorations="Underline" Margin="0,0,10,0"/>
                  <TextBlock x:Name="ArchSelectNone" Text="Clear" FontSize="10"
                             Foreground="#6b7280" Cursor="Hand" TextDecorations="Underline"/>
                </StackPanel>
              </Grid>
              <ScrollViewer MaxHeight="120" VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="ArchLinksPanel"/>
              </ScrollViewer>
              <TextBlock Text="ARCHITECTURE LINKED VIEW" Style="{StaticResource SectionLabel}" Margin="0,10,0,6"/>
              <ComboBox x:Name="ArchLinkedViewCombo" Style="{StaticResource ComboStyle}"/>
            </StackPanel>
          </Border>
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <Grid>
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="STRUCTURE LINKS (SELECT ONE OR MORE)"
                           Style="{StaticResource SectionLabel}" VerticalAlignment="Center"/>
                <StackPanel Grid.Column="1" Orientation="Horizontal">
                  <TextBlock x:Name="StructSelectAll" Text="Select All" FontSize="10"
                             Foreground="#1e248c" Cursor="Hand" TextDecorations="Underline" Margin="0,0,10,0"/>
                  <TextBlock x:Name="StructSelectNone" Text="Clear" FontSize="10"
                             Foreground="#6b7280" Cursor="Hand" TextDecorations="Underline"/>
                </StackPanel>
              </Grid>
              <ScrollViewer MaxHeight="120" VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="StructLinksPanel"/>
              </ScrollViewer>
              <TextBlock Text="STRUCTURE LINKED VIEW" Style="{StaticResource SectionLabel}" Margin="0,10,0,6"/>
              <ComboBox x:Name="StructLinkedViewCombo" Style="{StaticResource ComboStyle}"/>
            </StackPanel>
          </Border>
          <TextBlock x:Name="Step2Error" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        </StackPanel>

        <!-- STEP 3: OPTIONS / TRAFFIC LINK / SCOPE BOX -->
        <StackPanel x:Name="Step3Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="TRAFFIC LINK (OPTIONAL)" Style="{StaticResource SectionLabel}"/>
              <CheckBox x:Name="TrafficCheck" FontSize="12"
                        Content="Show Parking &amp; Elevations from Traffic Link"/>
              <StackPanel x:Name="TrafficPanel" Margin="0,10,0,0" Visibility="Collapsed">
                <ComboBox x:Name="TrafficCombo" Style="{StaticResource ComboStyle}"/>
                <TextBlock Text="TRAFFIC LINKED VIEW" Style="{StaticResource SectionLabel}" Margin="0,10,0,6"/>
                <ComboBox x:Name="TrafficLinkedViewCombo" Style="{StaticResource ComboStyle}"/>
              </StackPanel>
            </StackPanel>
          </Border>
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="SCOPE BOX (OPTIONAL)" Style="{StaticResource SectionLabel}"/>
              <TextBlock Text="Crops the coordination view and sheet to this scope box."
                         FontSize="10.5" Foreground="#8b93a7" TextWrapping="Wrap" Margin="0,0,0,6"/>
              <ComboBox x:Name="ScopeBoxCombo" Style="{StaticResource ComboStyle}"/>
            </StackPanel>
          </Border>
          <TextBlock x:Name="Step3Error" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        </StackPanel>

        <!-- STEP 4: FINAL SUMMARY -->
        <StackPanel x:Name="Step4Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="READY TO APPLY" Style="{StaticResource SectionLabel}"/>
              <TextBlock x:Name="SumView"    FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumArch"    FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumStruct"  FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumTraffic" FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock Text="Click Apply to set up the template, links, filters and coordination sheet."
                         FontSize="11" Foreground="#8b93a7" TextWrapping="Wrap" Margin="0,10,0,0"/>
            </StackPanel>
          </Border>
        </StackPanel>

      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Grid Grid.Row="4" Margin="20,10,20,18">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBlock x:Name="StepXOfY" Grid.Column="0" VerticalAlignment="Center"
                 FontSize="11" Foreground="#9aa0ac" FontWeight="SemiBold"/>
      <StackPanel Grid.Column="1" Orientation="Horizontal">
        <Button x:Name="BtnCancel" Content="Cancel" Style="{StaticResource GhostBtn}" Width="90" Margin="0,0,10,0"/>
        <Button x:Name="BtnBack"   Content="◄ Back" Style="{StaticResource GhostBtn}" Width="90" Margin="0,0,10,0"/>
        <Button x:Name="BtnNext"   Content="Next ►" Style="{StaticResource PrimaryBtn}" Width="120"/>
      </StackPanel>
    </Grid>
  </Grid>
</Window>
"""


def _brush(hex_color):
    return WM.BrushConverter().ConvertFromString(hex_color)


STEP_LABELS = (u"Select Views", u"Select Links", u"Options", u"Final Summary")


class LinkPickerDialog(object):
    """4-step wizard: Select Views -> Select Links -> Options/Traffic -> Summary.
    Architecture and Structure each support selecting multiple links, and
    (added 2026-08-31, batch mode) Step 1 supports selecting multiple VIEWS
    to process — every checked view shares the SAME Architecture/Structure/
    Traffic link selection from Step 2/3. That's a deliberate simplification,
    not an oversight: a project can have per-building link files (e.g.
    BLD_5 vs BLD_7), so one global selection can be wrong for some of the
    chosen views — accepted per explicit instruction ("worst case, re-run"
    for whichever view needed different links) rather than building
    per-view smart link matching.
    defaults = {'arch': (list_of_names, source_label), 'struct': (...),
    'traffic': (name_or_None, source_label), 'use_traffic': bool}"""

    def __init__(self, links, defaults, view_info, scope_boxes, batch_views, active_view):
        self.links         = links
        self.defaults      = defaults
        self.view_info     = view_info
        self.scope_boxes   = scope_boxes
        self.batch_views   = batch_views
        self.active_view   = active_view
        self.cancelled     = True
        self.arch_links    = []
        self.struct_links  = []
        self.traffic_link  = None
        self.use_traffic   = False
        self.scope_box     = None
        self.selected_views      = []
        self.arch_linked_view    = None
        self.struct_linked_view  = None
        self.traffic_linked_view = None
        self._window       = None
        self._by_name      = {}
        self._scope_by_name = {}
        self._view_by_name  = {}
        self._view_checks   = []
        self._arch_checks  = []
        self._struct_checks = []
        self._arch_view_by_label    = {}
        self._struct_view_by_label  = {}
        self._traffic_view_by_label = {}
        self._step         = 1

    def _build(self):
        ctx    = SysXmlReader.Create(StringReader(PICKER_XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        # Step 1 — views to process (multi-select, batch mode). Only the
        # active view is pre-checked, matching the tool's original
        # single-view behavior by default; checking more views applies the
        # SAME Step 2/3 choices to every one of them.
        view_names = sorted((bv[u"name"] for bv in self.batch_views), key=lambda n: n.upper())
        view_by_name = {}
        for bv in self.batch_views:
            view_by_name.setdefault(bv[u"name"], bv)
        self._view_by_name = view_by_name
        active_name = self.view_info.get(u"name", u"")
        self._view_checks = self._build_checklist(u"ViewsPanel", view_names, [active_name])

        w.FindName(u"ViewSelectAll").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._view_checks, True))
        w.FindName(u"ViewSelectNone").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._view_checks, False))

        # Step 2 — link checklists (multi-select). Nested links (see
        # get_all_link_instances' is_nested docstring) are excluded from
        # the selectable names here — they're never something a user
        # should pick as "this is my Architecture/Structure/Traffic link"
        # — but self.links / self._by_name below stay unfiltered, so Step
        # 5's hide sweep in run() still treats an unselected nested link
        # like any other "other" link.
        names = sorted((li[u"name"] for li in self.links if not li.get(u"is_nested")),
                        key=lambda n: n.upper())
        by_name = {}
        for li in self.links:
            by_name.setdefault(li[u"name"], li)
        self._by_name = by_name

        arch_default_names, _arch_src     = self.defaults.get(u"arch", ([], u"no match"))
        struct_default_names, _struct_src = self.defaults.get(u"struct", ([], u"no match"))
        traffic_name, _traffic_src        = self.defaults.get(u"traffic", (None, u"no match"))

        self._arch_checks   = self._build_checklist(u"ArchLinksPanel", names, arch_default_names)
        self._struct_checks = self._build_checklist(u"StructLinksPanel", names, struct_default_names)

        traffic_combo = w.FindName(u"TrafficCombo")
        for n in names:
            traffic_combo.Items.Add(n)
        if traffic_name:
            traffic_combo.SelectedItem = traffic_name

        # Smart Linked View — elevation-matched, '#'-prioritized view picker
        # per role (see _find_matching_link_views). Repopulated live on every
        # relevant checkbox/combo change, not just once "on load", so the
        # list never goes stale if the user changes which link(s) are
        # selected for a role after the wizard opens.
        host_level = self.view_info.get(u"level_obj")
        arch_view_combo    = w.FindName(u"ArchLinkedViewCombo")
        struct_view_combo  = w.FindName(u"StructLinkedViewCombo")
        traffic_view_combo = w.FindName(u"TrafficLinkedViewCombo")

        def _refresh_arch_views(sender, e):
            selected = [self._by_name[n] for n in self._checked_names(self._arch_checks)]
            matches = _find_matching_link_views(selected, host_level)
            self._arch_view_by_label = _populate_linked_view_combo(arch_view_combo, matches)

        def _refresh_struct_views(sender, e):
            selected = [self._by_name[n] for n in self._checked_names(self._struct_checks)]
            matches = _find_matching_link_views(selected, host_level)
            self._struct_view_by_label = _populate_linked_view_combo(struct_view_combo, matches)

        def _refresh_traffic_views(sender, e):
            sel_name = traffic_combo.SelectedItem
            selected = [self._by_name[sel_name]] if sel_name else []
            matches = _find_matching_link_views(selected, host_level)
            self._traffic_view_by_label = _populate_linked_view_combo(traffic_view_combo, matches)

        for cb in self._arch_checks:
            cb.Checked   += _refresh_arch_views
            cb.Unchecked += _refresh_arch_views
        for cb in self._struct_checks:
            cb.Checked   += _refresh_struct_views
            cb.Unchecked += _refresh_struct_views
        traffic_combo.SelectionChanged += _refresh_traffic_views

        _refresh_arch_views(None, None)
        _refresh_struct_views(None, None)
        _refresh_traffic_views(None, None)

        # Scope box — "None" always first/default
        scope_combo = w.FindName(u"ScopeBoxCombo")
        none_label = u"None"
        scope_combo.Items.Add(none_label)
        self._scope_by_name = {}
        for sb in self.scope_boxes:
            scope_combo.Items.Add(sb[u"name"])
            self._scope_by_name.setdefault(sb[u"name"], sb)
        scope_combo.SelectedItem = none_label

        # Step 3 — traffic option
        traffic_check = w.FindName(u"TrafficCheck")
        traffic_panel = w.FindName(u"TrafficPanel")
        use_traffic_default = bool(self.defaults.get(u"use_traffic"))
        traffic_check.IsChecked = use_traffic_default
        traffic_panel.Visibility = (System.Windows.Visibility.Visible if use_traffic_default
                                     else System.Windows.Visibility.Collapsed)
        traffic_check.Checked   += lambda s, e: setattr(
            traffic_panel, u"Visibility", System.Windows.Visibility.Visible)
        traffic_check.Unchecked += lambda s, e: setattr(
            traffic_panel, u"Visibility", System.Windows.Visibility.Collapsed)

        w.FindName(u"BtnGear").Click   += self._on_settings
        w.FindName(u"BtnCloseX").Click += lambda s, e: window.Close()
        w.FindName(u"BtnCancel").Click += lambda s, e: window.Close()
        w.FindName(u"BtnBack").Click   += self._on_back
        w.FindName(u"BtnNext").Click   += self._on_next

        # "Select All" / "Clear" — plain TextBlocks styled as links (not
        # Button, to stay visually small/secondary next to the section
        # label) wired via MouseLeftButtonUp, same pattern XamlReader-loaded
        # XAML already uses for every other click handler in this class.
        w.FindName(u"ArchSelectAll").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._arch_checks, True))
        w.FindName(u"ArchSelectNone").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._arch_checks, False))
        w.FindName(u"StructSelectAll").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._struct_checks, True))
        w.FindName(u"StructSelectNone").MouseLeftButtonUp += (
            lambda s, e: self._set_all_checked(self._struct_checks, False))

        self._go_to_step(1)
        return window

    def _build_checklist(self, panel_name, names, checked_names):
        panel = self._window.FindName(panel_name)
        panel.Children.Clear()
        checked_set = set(checked_names or [])
        checks = []
        for n in names:
            cb = WC.CheckBox()
            cb.Content = n
            cb.FontSize = 12
            cb.Margin = System.Windows.Thickness(0, 3, 0, 3)
            cb.IsChecked = n in checked_set
            panel.Children.Add(cb)
            checks.append(cb)
        return checks

    def _checked_names(self, checks):
        return [cb.Content for cb in checks if cb.IsChecked]

    def _set_all_checked(self, checks, value):
        # Setting IsChecked fires each checkbox's own Checked/Unchecked
        # event, which already refreshes that role's Smart Linked View
        # combo (see _refresh_arch_views/_refresh_struct_views) — nothing
        # extra needed here.
        for cb in checks:
            cb.IsChecked = value

    def _on_settings(self, sender, e):
        """Gear icon — the two tools were merged into one button, this is
        where the settings editor now lives. run() reloads Settings.json
        fresh after the wizard closes, so anything changed here is picked
        up for this same run without needing to reopen the tool."""
        settings_ui.SettingsDialog(cfgmod.load_settings()).show()

    def _go_to_step(self, step):
        self._step = step
        w = self._window

        panels = {1: u"Step1Panel", 2: u"Step2Panel", 3: u"Step3Panel", 4: u"Step4Panel"}
        for i, name in panels.items():
            w.FindName(name).Visibility = (System.Windows.Visibility.Visible if i == step
                                            else System.Windows.Visibility.Collapsed)

        info = {
            1: u"Check every view you want set up with the SAME Architecture/Structure/Traffic "
               u"links chosen next — the active view is pre-checked. If one view actually needs "
               u"different links, just run this tool again for that view alone afterward.",
            2: u"Pick one or more Architecture links and one or more Structure links. Pre-checked "
               u"from your last run or auto-detected by keyword — change either if needed. Each "
               u"card also shows a matching linked view (by level elevation) for reference — it "
               u"does not change that link's cut plane, so this tool's red/blue coloring always "
               u"stays correct.",
            3: u"Optional: also restrict a Traffic link to Parking only, shown in halftone. If "
               u"you pick a linked view here, unlike Architecture/Structure, it DOES drive that "
               u"link's display — only pick one you know shows nothing but Parking, including "
               u"annotations.",
            4: u"Review your choices, then click Apply to set up coordination graphics and "
               u"create the sheet.",
        }
        w.FindName(u"InfoText").Text = info[step]

        w.FindName(u"BtnBack").Visibility = (System.Windows.Visibility.Collapsed if step == 1
                                              else System.Windows.Visibility.Visible)
        w.FindName(u"BtnNext").Content = u"Apply" if step == 4 else u"Next ►"
        w.FindName(u"StepXOfY").Text = u"Step {} of 4 — {}".format(step, STEP_LABELS[step - 1])

        self._update_step_indicator()

        if step == 4:
            self._populate_summary()

    def _update_step_indicator(self):
        w = self._window
        for i in range(1, 5):
            badge  = w.FindName(u"Badge{}".format(i))
            num_tb = w.FindName(u"BadgeNum{}".format(i))
            if i < self._step:
                badge.Background  = _brush(u"#44b8d3")
                num_tb.Text       = u"✓"
                num_tb.Foreground = _brush(u"#FFFFFF")
            elif i == self._step:
                badge.Background  = _brush(u"#1e248c")
                num_tb.Text       = unicode(i)
                num_tb.Foreground = _brush(u"#FFFFFF")
            else:
                badge.Background  = _brush(u"#e1e8ed")
                num_tb.Text       = unicode(i)
                num_tb.Foreground = _brush(u"#9aa0ac")
        for i in range(1, 4):
            conn = w.FindName(u"Connector{}".format(i))
            conn.Background = _brush(u"#44b8d3") if i < self._step else _brush(u"#e1e8ed")

    def _populate_summary(self):
        w = self._window
        arch_names   = self._checked_names(self._arch_checks)
        struct_names = self._checked_names(self._struct_checks)
        use_traffic  = bool(w.FindName(u"TrafficCheck").IsChecked)
        traffic_name = w.FindName(u"TrafficCombo").SelectedItem if use_traffic else None

        arch_view    = w.FindName(u"ArchLinkedViewCombo").SelectedItem or NONE_LINKED_VIEW_LABEL
        struct_view  = w.FindName(u"StructLinkedViewCombo").SelectedItem or NONE_LINKED_VIEW_LABEL
        traffic_view = (w.FindName(u"TrafficLinkedViewCombo").SelectedItem or NONE_LINKED_VIEW_LABEL
                        if use_traffic else None)

        view_names_checked = self._checked_names(self._view_checks)
        w.FindName(u"SumView").Text    = u"View(s): {}".format(u", ".join(view_names_checked) or u"—")
        w.FindName(u"SumArch").Text    = u"Architecture link(s): {} — linked view: {}".format(
            u", ".join(arch_names) or u"—", arch_view)
        w.FindName(u"SumStruct").Text  = u"Structure link(s): {} — linked view: {}".format(
            u", ".join(struct_names) or u"—", struct_view)
        w.FindName(u"SumTraffic").Text = (u"Traffic link: {} — linked view: {}".format(traffic_name, traffic_view)
                                           if (use_traffic and traffic_name)
                                           else u"Traffic link: (not used)")

    def _on_back(self, sender, e):
        if self._step > 1:
            self._go_to_step(self._step - 1)

    def _on_next(self, sender, e):
        w = self._window

        if self._step == 1:
            err = w.FindName(u"Step1Error")
            if not self._checked_names(self._view_checks):
                err.Text = u"Select at least one view to process."
                return
            err.Text = u""

        elif self._step == 2:
            err = w.FindName(u"Step2Error")
            arch_names   = self._checked_names(self._arch_checks)
            struct_names = self._checked_names(self._struct_checks)
            if not arch_names or not struct_names:
                err.Text = u"Select at least one Architecture link and at least one Structure link."
                return
            overlap = set(arch_names) & set(struct_names)
            if overlap:
                err.Text = u"{} cannot be both an Architecture and a Structure link.".format(
                    u", ".join(sorted(overlap)))
                return
            err.Text = u""

        elif self._step == 3:
            err = w.FindName(u"Step3Error")
            use_traffic = bool(w.FindName(u"TrafficCheck").IsChecked)
            if use_traffic:
                traffic_name = w.FindName(u"TrafficCombo").SelectedItem
                arch_names   = self._checked_names(self._arch_checks)
                struct_names = self._checked_names(self._struct_checks)
                if not traffic_name:
                    err.Text = u"Select a Traffic link, or uncheck the Traffic option."
                    return
                if traffic_name in arch_names or traffic_name in struct_names:
                    err.Text = u"The Traffic link must be different from the Architecture and Structure links."
                    return
            err.Text = u""

        elif self._step == 4:
            self._finish()
            return

        self._go_to_step(self._step + 1)

    def _finish(self):
        w = self._window
        view_names   = self._checked_names(self._view_checks)
        arch_names   = self._checked_names(self._arch_checks)
        struct_names = self._checked_names(self._struct_checks)
        use_traffic  = bool(w.FindName(u"TrafficCheck").IsChecked)
        traffic_name = w.FindName(u"TrafficCombo").SelectedItem if use_traffic else None
        scope_name   = w.FindName(u"ScopeBoxCombo").SelectedItem

        self.selected_views = [self._view_by_name[n][u"view"] for n in view_names]
        self.arch_links    = [self._by_name[n] for n in arch_names]
        self.struct_links  = [self._by_name[n] for n in struct_names]
        self.traffic_link  = self._by_name[traffic_name] if (use_traffic and traffic_name) else None
        self.use_traffic   = use_traffic
        self.scope_box     = self._scope_by_name.get(scope_name)

        arch_view_label    = w.FindName(u"ArchLinkedViewCombo").SelectedItem
        struct_view_label  = w.FindName(u"StructLinkedViewCombo").SelectedItem
        traffic_view_label = w.FindName(u"TrafficLinkedViewCombo").SelectedItem if use_traffic else None

        self.arch_linked_view    = self._arch_view_by_label.get(arch_view_label)
        self.struct_linked_view  = self._struct_view_by_label.get(struct_view_label)
        self.traffic_linked_view = (self._traffic_view_by_label.get(traffic_view_label)
                                     if use_traffic else None)

        self.cancelled = False
        w.Close()

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
        window = self._build()
        frame  = DispatcherFrame()

        def on_closed(s, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        Dispatcher.PushFrame(frame)


# ─────────────────────────────────────────────────────────────────────────────
# LINK IDENTIFICATION  (keyword auto-detect + persisted memory, feeds the wizard's Step 2/3 defaults)
# ─────────────────────────────────────────────────────────────────────────────

def get_all_scope_boxes():
    """One dict per Scope Box (OST_VolumeOfInterest instance) in the host doc."""
    boxes = []
    try:
        elems = (DB.FilteredElementCollector(doc)
                   .OfCategory(DB.BuiltInCategory.OST_VolumeOfInterest)
                   .WhereElementIsNotElementType()
                   .ToElements())
    except Exception:
        elems = []
    for e in elems:
        boxes.append({u"instance": e, u"id": e.Id, u"name": _elem_name(e)})
    return boxes


def apply_scope_box(view, scope_box, warnings):
    """scope_box is a dict from get_all_scope_boxes(), or None for 'no crop
    change'. BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP is the
    documented way to assign a Scope Box to a view/template programmatically."""
    if scope_box is None:
        return
    bip = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)
    if bip is None:
        warnings.append(u"VIEWER_VOLUME_OF_INTEREST_CROP is not available in this "
                         u"Revit version — could not apply the scope box '{}'.".format(
                             scope_box[u"name"]))
        return
    try:
        p = view.get_Parameter(bip)
        if p is None or p.IsReadOnly:
            warnings.append(u"The active view has no editable scope-box parameter — "
                             u"could not apply '{}'.".format(scope_box[u"name"]))
            return
        p.Set(scope_box[u"id"])
    except Exception as ex:
        warnings.append(u"Could not apply scope box '{}': {}".format(scope_box[u"name"], ex))


# Added above/below the lowest/highest Level found (see
# _building_elevation_range) before cropping a Section to "the building's
# height" — a bare level-to-level crop would clip roof parapets/mechanical
# equipment above the top level and footings below the bottom one. ~1m,
# Revit's internal units are always feet regardless of the project's
# display units.
SECTION_HEIGHT_CROP_MARGIN_FT = 3.28084


def _building_elevation_range(arch_links, struct_links, warnings):
    """Lowest and highest Level elevation found across EVERY selected
    Architecture and Structure link, in HOST document coordinates (each
    link's own Level.Elevation run through RevitLinkInstance.
    GetTotalTransform(), same pattern _find_matching_link_views already
    uses for level-elevation matching). Combines both roles deliberately
    — "the building's height" per explicit request, and either discipline
    can have a level the other doesn't (e.g. Structure's own foundation
    level, or an Architecture-only roof/parapet level). Returns
    (min_elev, max_elev), or (None, None) if no Level could be read at
    all from any selected link."""
    min_elev = None
    max_elev = None
    for li in (list(arch_links) + list(struct_links)):
        link_doc = li.get(u"doc")
        inst     = li.get(u"instance")
        if link_doc is None or inst is None:
            continue
        try:
            transform = inst.GetTotalTransform()
        except Exception:
            continue
        try:
            levels = list(DB.FilteredElementCollector(link_doc).OfClass(DB.Level))
        except Exception:
            levels = []
        for lvl in levels:
            try:
                z = transform.OfPoint(DB.XYZ(0.0, 0.0, lvl.Elevation)).Z
            except Exception:
                continue
            if min_elev is None or z < min_elev:
                min_elev = z
            if max_elev is None or z > max_elev:
                max_elev = z
    if min_elev is None or max_elev is None:
        warnings.append(u"Could not read any Level from the selected Architecture/Structure "
                         u"links — Section crop height left unchanged.")
        return None, None
    return min_elev, max_elev


def _apply_section_height_crop(view, min_elev, max_elev, warnings):
    """Sets a Section view's crop region's VERTICAL extent to span
    min_elev..max_elev (HOST coordinates, plus SECTION_HEIGHT_CROP_MARGIN_
    FT on each side) — the horizontal extent (view-local X) and view depth
    (view-local Z, the far/near clip) are left exactly as they already
    are, only the height changes, per explicit request ("just the
    height").

    A Section's CropBox.Transform maps VIEW-LOCAL coordinates to the
    model: for a standard vertical building section (the overwhelming
    majority — created via the plain Section tool, not an unusually
    rotated/sloped one), BasisY is exactly the model's vertical axis
    (0,0,1), which is what lets local Y be computed from model Z alone
    via the inverse transform, independent of X/Y choice. Checked
    explicitly before writing anything — a non-vertical section (BasisY
    not aligned with global Z) is left untouched with a warning instead
    of risking a nonsensical crop, since the simple "transform two points
    at these Z values" approach only holds for that standard case."""
    if min_elev is None or max_elev is None:
        return
    try:
        crop_box = view.CropBox
        transform = crop_box.Transform
    except Exception as ex:
        warnings.append(u"Could not read the section's crop box: {}".format(ex))
        return

    try:
        basis_y = transform.BasisY
        if abs(basis_y.Z) < 0.999:
            warnings.append(
                u"This section's crop isn't a standard vertical one (its 'up' direction "
                u"isn't aligned with the model's vertical axis) — height-based cropping "
                u"skipped for it to avoid producing a nonsensical crop region.")
            return
    except Exception as ex:
        warnings.append(u"Could not check the section's crop orientation: {}".format(ex))
        return

    try:
        inverse = transform.Inverse
        low  = inverse.OfPoint(DB.XYZ(0.0, 0.0, min_elev - SECTION_HEIGHT_CROP_MARGIN_FT)).Y
        high = inverse.OfPoint(DB.XYZ(0.0, 0.0, max_elev + SECTION_HEIGHT_CROP_MARGIN_FT)).Y
        new_min_y, new_max_y = (low, high) if low <= high else (high, low)
        crop_box.Min = DB.XYZ(crop_box.Min.X, new_min_y, crop_box.Min.Z)
        crop_box.Max = DB.XYZ(crop_box.Max.X, new_max_y, crop_box.Max.Z)
        view.CropBox = crop_box
        view.CropBoxActive = True
    except Exception as ex:
        warnings.append(u"Could not set the section's crop height: {}".format(ex))


def get_all_link_instances():
    """One dict per placed RevitLinkInstance (not de-duplicated by file —
    each placed instance is a distinct pickable candidate).

    is_nested marks a link that was only loaded as a NESTED reference
    inside another link (an "Attachment"-type nested link can surface into
    the host document's own RevitLinkInstance collection, appearing
    alongside genuinely independent top-level links with no visual
    distinction) — confirmed via reflecting the installed RevitAPI.dll:
    RevitLinkType.IsNestedLink is a real, documented property, not a
    guess. These are excluded from the wizard's selectable Architecture/
    Structure/Traffic lists (see LinkPickerDialog._build) — a user picking
    "which link is Architecture" should never be offered something that
    only exists because it happens to be nested inside another link, not
    because it was independently, deliberately linked into this project.
    They still count as an "other" link for Step 5's hide sweep if not
    otherwise selected (this function's OWN result set is unfiltered;
    only the wizard's checklist-building filters is_nested out)."""
    links = []
    for inst in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
        link_doc = None
        try:
            link_doc = inst.GetLinkDocument()
        except Exception:
            pass
        is_nested = False
        try:
            link_type = doc.GetElement(inst.GetTypeId())
            is_nested = bool(link_type.IsNestedLink)
        except Exception:
            pass
        name  = _elem_name(inst)
        title = link_doc.Title if link_doc is not None else name
        links.append({
            u"instance" : inst,
            u"id"       : inst.Id,
            u"doc"      : link_doc,
            u"name"     : name,
            u"title"    : title,
            u"loaded"   : link_doc is not None,
            u"is_nested": is_nested,
        })
    return links


# ─────────────────────────────────────────────────────────────────────────────
# SMART LINKED VIEW  (elevation-matched, #-prioritized per-link view picker)
# ─────────────────────────────────────────────────────────────────────────────

LEVEL_ELEVATION_TOLERANCE_FT = 1.0
# ~300mm — generous enough to bridge typical Architecture-finished-floor vs.
# Structure-top-of-slab datum offsets, tight enough not to conflate two
# genuinely different stories. Compared in the HOST document's internal
# coordinate system (each link's own Level.Elevation run through
# RevitLinkInstance.GetTotalTransform()), not raw Level.Elevation-to-
# Level.Elevation — a link's own internal origin/Project Base Point can
# differ from the host's even when both are placed correctly.

NONE_LINKED_VIEW_LABEL     = u"<None (By Host View)>"
NO_MATCH_LINKED_VIEW_LABEL = u"<No found matching views>"


def _find_matching_link_views(link_infos, host_level):
    """link_infos: link dicts (from get_all_link_instances()) currently
    selected for one role — may be more than one for Architecture/Structure.
    host_level: the active view's Level (view.GenLevel), or None.

    For each link, finds the SINGLE closest Level within
    LEVEL_ELEVATION_TOLERANCE_FT of the host level, then collects every
    ViewPlan on that level, excluding templates and dependent views
    (View.GetPrimaryViewId() != InvalidElementId means "this is a dependent
    view of some other primary view" — skipped). Returns a flat list of
    {'view':, 'link':, 'name':} across all given links, sorted so any view
    name starting with '#' sorts first (then alphabetically)."""
    if host_level is None:
        return []
    try:
        host_elev = host_level.Elevation
    except Exception:
        return []

    results = []
    for li in link_infos:
        link_doc = li.get(u"doc")
        inst     = li.get(u"instance")
        if link_doc is None or inst is None:
            continue
        try:
            transform = inst.GetTotalTransform()
        except Exception:
            continue

        try:
            levels = list(DB.FilteredElementCollector(link_doc).OfClass(DB.Level))
        except Exception:
            levels = []
        best_level_id = None
        best_diff = None
        for lvl in levels:
            try:
                pt = transform.OfPoint(DB.XYZ(0.0, 0.0, lvl.Elevation))
                diff = abs(pt.Z - host_elev)
                if diff <= LEVEL_ELEVATION_TOLERANCE_FT and (best_diff is None or diff < best_diff):
                    best_diff = diff
                    best_level_id = lvl.Id.IntegerValue
            except Exception:
                continue
        if best_level_id is None:
            continue

        try:
            plans = list(DB.FilteredElementCollector(link_doc).OfClass(DB.ViewPlan))
        except Exception:
            plans = []
        for vp in plans:
            try:
                if vp.IsTemplate:
                    continue
                gen_level = vp.GenLevel
                if gen_level is None or gen_level.Id.IntegerValue != best_level_id:
                    continue
                if vp.GetPrimaryViewId() != DB.ElementId.InvalidElementId:
                    continue  # dependent view
                # ViewPlan also covers Ceiling Plans and Area Plans, which
                # are never sensible candidates for "how this link displays"
                # (a ceiling plan shows reflected content, an area plan shows
                # almost nothing structural) — excluded outright rather than
                # just deprioritized. FloorPlan/EngineeringPlan only — the
                # latter is what Revit's own "Structural Plan" template
                # produces, so Structure links need it included too.
                if vp.ViewType not in (DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan):
                    continue
                is_floor_plan = (vp.ViewType == DB.ViewType.FloorPlan)
                is_coarse = (vp.DetailLevel == DB.ViewDetailLevel.Coarse)
            except Exception:
                continue
            results.append({u"view": vp, u"link": li, u"name": _elem_name(vp) or u"?",
                             u"is_floor_plan": is_floor_plan, u"is_coarse": is_coarse})

    # Medium/Fine-detail views ALWAYS sort ahead of Coarse ones — even a
    # '#'-prefixed view loses to a plain non-Coarse one, per explicit
    # feedback: a Coarse view should never win the auto-selected default
    # just because it happens to start with '#'. (Coarse still makes
    # Revit show a linked element's own hardcoded "Coarse Scale Fill
    # Pattern" for OTHER things beyond the wall/column fill this tool's
    # OverrideGraphicSettings.SetDetailLevel(Fine) already neutralizes —
    # doors/windows/simplified linework etc. — so avoiding it as a default
    # is still worth doing even after that fix.) Within the same Coarse-
    # status tier: '#'-prefixed first, then a genuine Floor Plan preferred
    # over a Ceiling/Area/Structural/Engineering plan on the same level,
    # then alphabetical.
    results.sort(key=lambda r: (1 if r[u"is_coarse"] else 0,
                                 0 if r[u"name"].startswith(u"#") else 1,
                                 0 if r[u"is_floor_plan"] else 1,
                                 r[u"name"].upper()))
    return results


def _populate_linked_view_combo(combo, matches):
    """Fills `combo` per spec (None-option + matches, or exactly one "no
    match" item) and auto-selects the first real view (already '#'-
    prioritized, non-Coarse-preferred by _find_matching_link_views' sort)
    — or the None option when nothing was found. A Coarse-detail candidate
    gets a visible warning suffix right in the label — see the module
    docstring / _apply_smart_linked_view for why Coarse detail level on a
    linked view can make Revit show a linked element's own hardcoded
    "Coarse Scale Fill Pattern" from its Type Properties instead of this
    tool's View Filter hatch. Returns {label: match_dict} so the caller can
    resolve the chosen label back to its (view, link) pair at Apply time;
    a chosen label of NONE_LINKED_VIEW_LABEL/NO_MATCH_LINKED_VIEW_LABEL is
    simply absent from this dict, which is exactly "unassigned" to callers
    that do by_label.get(selected_label)."""
    combo.Items.Clear()
    if not matches:
        combo.Items.Add(NO_MATCH_LINKED_VIEW_LABEL)
        combo.SelectedIndex = 0
        return {}

    by_label = {}
    seen_names = set()
    combo.Items.Add(NONE_LINKED_VIEW_LABEL)
    for m in matches:
        base = m[u"name"]
        if base in seen_names:
            base = u"{} ({})".format(m[u"name"], m[u"link"][u"name"])
        seen_names.add(m[u"name"])
        label = (u"{} — Coarse detail, may show solid fill".format(base)
                 if m.get(u"is_coarse") else base)
        by_label[label] = m
        combo.Items.Add(label)
    combo.SelectedIndex = 1
    return by_label


def _matches_any(link_info, keywords):
    hay = u"{} {}".format(link_info[u"name"], link_info[u"title"]).upper()
    for kw in (keywords or []):
        if kw and kw.upper() in hay:
            return True
    return False


def _role_default(links, memory_uid, keywords):
    """(name_or_None, source_label) — remembered choice first, else a
    keyword auto-detect only if it's unambiguous."""
    if memory_uid:
        for li in links:
            try:
                if li[u"instance"].UniqueId == memory_uid:
                    return li[u"name"], u"remembered"
            except Exception:
                continue

    candidates = [li for li in links if _matches_any(li, keywords)]
    if len(candidates) == 1:
        return candidates[0][u"name"], u"auto-detected"
    if len(candidates) > 1:
        return None, u"{} keyword matches — pick one".format(len(candidates))
    return None, u"no keyword match"


def _role_default_multi(links, memory_uids, keywords):
    """(list_of_names, source_label) for a multi-select role (Architecture/
    Structure) — remembered set first (any UID that still resolves), else
    every keyword match is pre-checked (the user narrows down, not just
    picks one from an ambiguous set)."""
    if memory_uids:
        uid_set = set(memory_uids)
        names = []
        for li in links:
            try:
                if li[u"instance"].UniqueId in uid_set:
                    names.append(li[u"name"])
            except Exception:
                continue
        if names:
            return names, u"remembered"

    candidates = [li[u"name"] for li in links if _matches_any(li, keywords)]
    if candidates:
        return candidates, u"{} keyword match{}".format(
            len(candidates), u"" if len(candidates) == 1 else u"es")
    return [], u"no keyword match"


# ─────────────────────────────────────────────────────────────────────────────
# VIEW HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _is_section_view(view):
    """True for a genuine Section view specifically — NOT Elevation or
    Detail, which share the same ViewSection class in the API but need a
    ViewType check to tell apart. Sections get a different, dedicated
    template (SECTION_TEMPLATE_NAME) with different category/filter
    treatment — see run()'s per-view branch and the module docstring's
    section on this."""
    try:
        return isinstance(view, DB.ViewSection) and view.ViewType == DB.ViewType.Section
    except Exception:
        return False


def _is_eligible_coordination_view(view):
    try:
        if view is None or view.IsTemplate:
            return False
    except Exception:
        return False
    try:
        if isinstance(view, DB.ViewPlan):
            return True
    except Exception:
        pass
    return _is_section_view(view)


def get_view_and_validate():
    view = doc.ActiveView
    if not _is_eligible_coordination_view(view):
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics",
            u"Open a 2D plan view (Floor Plan / Ceiling Plan / Area Plan) or a "
            u"Section — not a view template, 3D view, elevation, detail or sheet — "
            u"then run this command again."
        )
        return None
    return view


def get_eligible_views_for_batch(active_view):
    """Every non-template ViewPlan or Section in the project (same
    eligibility as get_view_and_validate's single-view check), for Step
    1's multi-select batch checklist. One dict per view: {'view':,
    'name':}. active_view is always included even if, for some reason,
    the general collectors wouldn't otherwise surface it, so the
    pre-checked default never comes up empty. Plans and Sections CAN be
    mixed in the same batch selection — run() branches per-view on
    _is_section_view for the categories/filters/Traffic differences (see
    module docstring)."""
    seen_ids = set()
    out = []
    try:
        all_views = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan))
    except Exception:
        all_views = []
    try:
        all_views += [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSection)
                      if _is_section_view(v)]
    except Exception:
        pass
    for v in all_views:
        try:
            if v.IsTemplate:
                continue
        except Exception:
            continue
        try:
            vid = v.Id.IntegerValue
        except Exception:
            continue
        if vid in seen_ids:
            continue
        seen_ids.add(vid)
        out.append({u"view": v, u"name": _elem_name(v) or u"?"})
    if active_view is not None:
        try:
            aid = active_view.Id.IntegerValue
        except Exception:
            aid = None
        if aid is not None and aid not in seen_ids:
            out.append({u"view": active_view, u"name": _elem_name(active_view) or u"?"})
    return out


def is_basement_view(view):
    name = _elem_name(view).upper()
    for tok in BASEMENT_TOKENS:
        if tok.upper() in name:
            return True
    try:
        level = view.GenLevel
        if level is not None:
            lname = _elem_name(level).upper()
            for tok in BASEMENT_TOKENS:
                if tok.upper() in lname:
                    return True
            if level.Elevation < 0:
                return True
    except Exception:
        pass
    return False


def _hide_category_safe(target, bic, warnings, hide=True):
    """Checked live-testing finding: SetCategoryHidden(..., True) throws
    ArgumentException ("Category cannot be hidden") for some categories in
    some view types — e.g. OST_ElevationMarks in a Floor Plan — which
    matches a grayed-out, un-editable row in Revit's own V/G Overrides
    dialog for that exact view/category combination. That's not a bug to
    warn about every run — it's Revit itself saying this category has
    nothing to hide here. Checked via Category.get_AllowsVisibilityControl
    (confirmed real via reflecting the installed RevitAPI.dll) *before*
    attempting the hide, so the expected, un-fixable case exits silently
    instead of logging a warning; the try/except below still catches and
    warns about anything genuinely unexpected."""
    try:
        cat_id = DB.ElementId(bic)
        if hide:
            try:
                cat = DB.Category.GetCategory(doc, bic)
                if cat is not None and not cat.get_AllowsVisibilityControl(target):
                    try:
                        logger.info(u"Category '{}' does not allow visibility control on this "
                                    u"target — hide skipped silently (not a bug, see "
                                    u"_hide_category_safe's docstring).".format(bic.ToString()))
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        target.SetCategoryHidden(cat_id, hide)
    except Exception as ex:
        warnings.append(u"Could not {} category '{}': {}".format(
            u"hide" if hide else u"show", bic.ToString(), ex))


def _override_category_safe(target, bic, ogs, warnings):
    """Mirrors _hide_category_safe's AllowsVisibilityControl pre-check —
    live testing (2026-08-31) surfaced a huge cascade of "Category cannot
    be overridden" warnings once the generic gray-line pass started
    walking EVERY Model category (see _apply_coordination_categories):
    categories like OST_ProjectInformation, OST_AnalysisDisplayStyle,
    OST_MEPAnalyticalAirLoop etc. never had visible geometry to override
    in the first place, so SetCategoryOverrides throwing for them isn't a
    bug to warn about every run — same reasoning _hide_category_safe
    already applies to SetCategoryHidden."""
    try:
        cat_id = DB.ElementId(bic)
        try:
            cat = DB.Category.GetCategory(doc, bic)
            if cat is not None and not cat.get_AllowsVisibilityControl(target):
                return
        except Exception:
            pass
        target.SetCategoryOverrides(cat_id, ogs)
    except Exception as ex:
        warnings.append(u"Could not set the fallback color for category '{}': {}".format(
            bic.ToString(), ex))


def _ensure_room_tags_visible(target, warnings):
    room_tags = getattr(DB.BuiltInCategory, "OST_RoomTags", None)
    if room_tags is None:
        return
    _hide_category_safe(target, room_tags, warnings, hide=False)


# ─────────────────────────────────────────────────────────────────────────────
# VIEW TEMPLATE  (Step 3)
# ─────────────────────────────────────────────────────────────────────────────

def _find_view_template_by_name(name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if v.IsTemplate and _elem_name(v) == name:
            return v
    return None


def _ensure_rvt_links_not_template_controlled(template, warnings):
    """REVERSED AGAIN (2026-08-30) — see module docstring ("WHY 'V/G
    OVERRIDES RVT LINKS' IS NOW KEPT TEMPLATE-CONTROLLED" / its follow-up
    note). A prior round made this row template-CONTROLLED (removed its
    parameter Id from the non-controlled list) so a value written to the
    template's own link settings would propagate to every view using it,
    including a sheet's duplicated view. Live testing then found something
    much worse: once this row was template-controlled, Step 5's own
    view-level HideElements calls for OTHER (non-selected) links stopped
    sticking through to commit, and — after Step 5 was moved to run last
    to compensate — a SELECTED link (Architecture) started getting hidden
    that was never in Step 5's own target list at all. Whatever the exact
    mechanism, template control over this row and view-level per-link
    HideElements do not coexist safely. Fixed at the root instead: this
    function now ENSURES the row is EXCLUDED (added to the non-controlled
    list) so link visibility/display-mode are always plain view-specific
    state, same as Step 5's hides always were — and _apply_link_display_
    settings for Traffic now targets `view` directly (not `template`), so
    the original propagation concern is solved a different way: Duplicate
    (WithDetailing) already carries view-specific overrides (hides AND
    link display settings) onto a sheet's duplicated view, no template
    inheritance needed. Best-effort regardless: a failure here is
    informational only, it doesn't block anything else this tool does."""
    try:
        param_id = None
        for p in template.Parameters:
            if p.Definition is not None and p.Definition.Name == u"V/G Overrides RVT Links":
                param_id = p.Id
                break
        if param_id is None:
            warnings.append(
                u"Could not find the 'V/G Overrides RVT Links' template control row in "
                u"this Revit version — informational only, link visibility/halftone are "
                u"view-specific regardless of this setting.")
            return
        current_ids = list(template.GetNonControlledTemplateParameterIds())
        if not any(i.IntegerValue == param_id.IntegerValue for i in current_ids):
            current_ids.append(param_id)
            template.SetNonControlledTemplateParameterIds(SCG.List[DB.ElementId](current_ids))
    except Exception as ex:
        warnings.append(
            u"Could not exclude 'V/G Overrides RVT Links' from template control: {} "
            u"(informational only).".format(ex))


def _build_nonconcrete_fallback_ogs(warnings):
    """Explicit white Cut+Surface fill / gray line — the non-concrete
    fallback appearance, extracted out of _apply_coordination_categories
    so run() can ALSO build it for the two Section-only Type-Name fallback
    filters (FILTER_NAME_STRUCT_FALLBACK/ARCH_FALLBACK), not just the
    plan template's blanket category override. See that function's
    docstring for why a wall with NO override at all isn't safe to rely
    on (native hardcoded fills like "15_BLOCK"'s)."""
    fallback_ogs = DB.OverrideGraphicSettings()
    try:
        fallback_ogs.SetCutLineColor(GRAY_COLOR)
        fallback_ogs.SetProjectionLineColor(GRAY_COLOR)
    except Exception as ex:
        warnings.append(u"Could not set the non-concrete fallback's line color: {}".format(ex))
    try:
        fallback_ogs.SetCutBackgroundPatternVisible(False)
        fallback_ogs.SetSurfaceBackgroundPatternVisible(False)
        fallback_ogs.SetCutBackgroundPatternId(DB.ElementId.InvalidElementId)
        fallback_ogs.SetSurfaceBackgroundPatternId(DB.ElementId.InvalidElementId)
    except Exception:
        pass
    solid_fill_id = get_solid_fill_pattern_id()
    if solid_fill_id != DB.ElementId.InvalidElementId:
        try:
            fallback_ogs.SetCutForegroundPatternVisible(True)
            fallback_ogs.SetCutForegroundPatternId(solid_fill_id)
            fallback_ogs.SetCutForegroundPatternColor(WHITE_COLOR)
            fallback_ogs.SetSurfaceForegroundPatternVisible(True)
            fallback_ogs.SetSurfaceForegroundPatternId(solid_fill_id)
            fallback_ogs.SetSurfaceForegroundPatternColor(WHITE_COLOR)
        except Exception as ex:
            warnings.append(u"Could not set the non-concrete fallback's white fill: {}".format(ex))
    else:
        warnings.append(
            u"No solid fill pattern could be resolved for the non-concrete fallback — "
            u"those elements may still show their own native Type Properties fill.")
    return fallback_ogs


def _apply_coordination_categories(target, host_clutter_bics, link_model_bics, wallnoncore_bics,
                                    annotation_bics, override_bics, warnings, apply_fallback=True):
    """Category hides/overrides shared between the template path and the
    direct-to-view fallback below — SetCategoryHidden/SetCategoryOverrides
    (and, in the caller, AddFilter/SetFilterOverrides) all work identically
    whether `target` is a ViewTemplate or a plain View."""
    # At Coarse detail level Revit renders walls (and some other categories)
    # with the wall type's own "Coarse Scale Fill Pattern" instead of
    # respecting per-element/filter graphic overrides — this can make our
    # hatch overrides look solid regardless of the Foreground/Background
    # pattern fix. Fine forces Revit to actually honor the overrides.
    try:
        target.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception as ex:
        warnings.append(u"Could not set detail level to Fine: {}".format(ex))

    # REVERTED — forcing target.DisplayStyle = DB.DisplayStyle.HLR here
    # caused Revit itself to crash in live testing, not a catchable
    # exception (the try/except around it never even helped, since a crash
    # is not a .NET exception the CLR can catch — it's Revit's native
    # process going down). Whatever DisplayStyle actually needed for a
    # ViewTemplate specifically, setting it via this simple property
    # assignment is NOT safe -- do not re-add this without a confirmed-safe
    # mechanism and real live verification before pushing again, given the
    # cost of being wrong here is a full application crash, not a warning.

    for bic in host_clutter_bics:
        _hide_category_safe(target, bic, warnings)
    for bic in link_model_bics:
        _hide_category_safe(target, bic, warnings)
    for bic in wallnoncore_bics:
        _hide_category_safe(target, bic, warnings)
    for bic in annotation_bics:
        _hide_category_safe(target, bic, warnings)

    _ensure_room_tags_visible(target, warnings)

    # Refinement 1 — Grids stay visible as a category (so the links' grids
    # show through); the HOST's own grids are hidden separately, per-element,
    # in run() — see module docstring for why this can't be a category hide.
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    if grids_bic is not None:
        _hide_category_safe(target, grids_bic, warnings, hide=False)

    # REMOVED — the View-Filter-based "hide all Annotations" / "whitelist
    # Model categories" sweep (_apply_annotation_hide_filter/
    # _apply_model_whitelist_filter, still defined below but no longer
    # called from here) is CONFIRMED to crash Revit outright when the user
    # opens the Filters tab of a View/View Template Properties dialog —
    # live-tested, a hard application crash. Both filters were created with
    # NO ElementFilter rule at all (matches every element in their category
    # scope by category membership alone) — an unusual configuration a
    # normal user would never produce through Revit's own UI, and very
    # likely what Revit's own Filters-tab rendering code doesn't handle
    # safely. See _cleanup_stale_whitelist_filters (called from run()),
    # which actively deletes any of these filters a prior run already
    # created. Do not re-add these calls without confirming what
    # specifically crashes and a real fix, given the cost of being wrong
    # here is a full application crash, not a warning.

    # Non-concrete fallback for override_bics (Walls/Columns/Structural*) —
    # EXPLICIT white fill + gray line, by explicit request (reversing the
    # "zero override" decision from one round earlier on purpose): a wall
    # with NO override shows whatever its own Type Properties say, which
    # can be a hardcoded native color (e.g. "15_BLOCK"'s Coarse Scale Fill
    # Color = Red, diagnosed a few rounds back) — forcing white here makes
    # every non-concrete element look consistent regardless of what its
    # own file happens to have set. Same category-level mechanism
    # (SetCategoryOverrides) already used and proven safe for many rounds
    # — NOT a new View Filter, given the crash history with those; a
    # category override only applies to elements a Filter doesn't already
    # match (Filters always win), so this correctly leaves Structure/
    # Architecture's own colored elements alone.
    #
    # IMPORTANT: this must be set EVERY run, not just when non-empty —
    # the shared template is a REUSED, PERSISTENT object (found by name,
    # not recreated), so whatever a PRIOR run set here (gray, or nothing)
    # stays until something explicitly overwrites it. Confirmed live: just
    # removing the code that used to set gray did NOT clear it from a
    # template a prior run had already touched.
    #
    # apply_fallback=False (Sections only — see the SECTIONS constants
    # block's docstring): skips this blanket CATEGORY-level fallback
    # entirely, because it can't tell Architecture's elements apart from
    # Structure's (the same limitation FILTER_NAME_STRUCT/ARCH exist to
    # work around) and would blanket over Architecture's concrete elements
    # too, which Sections need left untouched for native rendering. Two
    # dedicated Type-Name FILTERS (FILTER_NAME_STRUCT_FALLBACK/ARCH_
    # FALLBACK, built in run()) take over this exact job for Sections
    # instead, scoped per-link like the concrete filters already are.
    if apply_fallback:
        fallback_ogs = _build_nonconcrete_fallback_ogs(warnings)
        for bic in override_bics:
            _override_category_safe(target, bic, fallback_ogs, warnings)

    # Every OTHER visible Model category — Stairs, Doors, Windows,
    # Furniture, Plumbing Fixtures, Railings, Curtain Walls, etc. — gets a
    # plain gray line-color override instead of its own native (often
    # black) line color, per explicit request. Only Cut/Projection line
    # color is touched, no fill pattern, since most of these categories
    # have no meaningful Cut fill in a floor plan anyway. Deliberately NOT
    # a fixed hardcoded category list (the ask was "stairs, furniture,
    # doors, sanitary, etc." — an open-ended "everything else"): every
    # Model category in the document is walked, skipping only what's
    # already specially handled above (concrete-classified structural
    # categories, host clutter already hidden, Step 6 link-model hides,
    # wall non-core, annotation). Same category-level limitation as
    # everything else in this file: this can't distinguish host content
    # from linked content, so it applies wherever `target` governs.
    already_handled_bics = (set(override_bics) | set(host_clutter_bics)
                             | set(link_model_bics) | set(wallnoncore_bics)
                             | set(annotation_bics))
    gray_line_ogs = DB.OverrideGraphicSettings()
    try:
        gray_line_ogs.SetCutLineColor(GRAY_COLOR)
        gray_line_ogs.SetProjectionLineColor(GRAY_COLOR)
    except Exception as ex:
        warnings.append(u"Could not set the generic gray line-color override: {}".format(ex))
    for cat in doc.Settings.Categories:
        try:
            if cat.CategoryType != DB.CategoryType.Model:
                continue
            bic = DB.BuiltInCategory(cat.Id.IntegerValue)
        except Exception:
            continue
        if bic in already_handled_bics:
            continue
        _override_category_safe(target, bic, gray_line_ogs, warnings)
        # Door/window swing arcs (and similar family-internal symbolic
        # lines) are commonly drawn on a SUBCATEGORY of their parent
        # category, not the parent category's own Cut/Projection geometry
        # directly — confirmed live: Doors/Windows kept showing black
        # swing lines even after this loop's override, while Plumbing
        # Fixtures (no such subcategory split in this project's families)
        # correctly turned gray. A category override only affects geometry
        # NOT assigned to a subcategory, so every subcategory needs the
        # same override too, or it keeps its native color regardless of
        # what the parent category is set to.
        try:
            for subcat in cat.SubCategories:
                try:
                    if not subcat.get_AllowsVisibilityControl(target):
                        continue
                except Exception:
                    pass
                try:
                    target.SetCategoryOverrides(subcat.Id, gray_line_ogs)
                except Exception as ex:
                    warnings.append(
                        u"Could not set the generic gray override for subcategory "
                        u"'{}': {}".format(_elem_name(subcat), ex))
        except Exception:
            pass


def ensure_view_template(view, host_clutter_bics, link_model_bics, wallnoncore_bics,
                          annotation_bics, override_bics, warnings,
                          template_name=TEMPLATE_NAME, old_names=_OLD_TEMPLATE_NAMES,
                          apply_fallback=True):
    """Returns a View-like target already configured with the coordination
    categories/overrides. Normally that's the shared `template_name`
    template (created once, refreshed on every run, reusable across any
    view) — TEMPLATE_NAME for Plans by default, or SECTION_TEMPLATE_NAME
    (with apply_fallback=False) for Sections, see run()'s per-view branch.
    Finding/creating/assigning it can legitimately fail — e.g.
    View.IsViewValidForTemplateCreation() is False for some view types/
    states — so on ANY failure here this falls back to applying the exact
    same categories/overrides directly to the active view instead of
    aborting the whole command (link visibility, filters and colors still
    all work in that case; the coordination look just isn't captured as a
    reusable template for other views this run). The full underlying
    exception is always logged to `warnings`, never swallowed to a generic
    message, so the real Revit API error is visible if this keeps failing."""
    template = _find_view_template_by_name(template_name)

    if template is None:
        for _old_name in old_names:
            _old = _find_view_template_by_name(_old_name)
            if _old is not None:
                try:
                    _old.Name = template_name
                    template = _old
                except Exception as ex:
                    warnings.append(
                        u"Found the old-named template '{}' but could not rename it "
                        u"to '{}': {}".format(_old_name, template_name, ex))
                break

    if template is None:
        try:
            is_valid = getattr(view, "IsViewValidForTemplateCreation", None)
            if is_valid is not None and not is_valid():
                raise Exception(
                    u"View.IsViewValidForTemplateCreation() returned False for the "
                    u"active view (e.g. a dependent view, or a view type that can't "
                    u"become a template).")
            result = view.CreateViewTemplate()
            # Confirmed via live testing: this returns the resolved View
            # object directly (not an ElementId as the API docs' summary
            # implies) — doc.GetElement(result) would then fail with
            # "TypeError: expected ElementId, got ViewPlan". Handle both
            # so this keeps working if that ever differs by Revit version.
            if isinstance(result, DB.ElementId):
                template = doc.GetElement(result)
            else:
                template = result
            template.Name = template_name
        except Exception:
            warnings.append(
                u"Could not create the '{}' view template — applying categories/"
                u"overrides directly to the active view instead (link visibility, "
                u"filters and colors still work; just not saved as a reusable "
                u"template this run). Underlying error:\n{}".format(
                    template_name, traceback.format_exc()))
            _apply_coordination_categories(view, host_clutter_bics, link_model_bics,
                                            wallnoncore_bics, annotation_bics,
                                            override_bics, warnings, apply_fallback=apply_fallback)
            return view

    _apply_coordination_categories(template, host_clutter_bics, link_model_bics,
                                    wallnoncore_bics, annotation_bics, override_bics, warnings,
                                    apply_fallback=apply_fallback)
    _ensure_rvt_links_not_template_controlled(template, warnings)

    try:
        view.ViewTemplateId = template.Id
    except Exception:
        warnings.append(
            u"Could not apply the '{}' template to the active view — applying "
            u"categories/overrides directly to the active view instead. Underlying "
            u"error:\n{}".format(template_name, traceback.format_exc()))
        _apply_coordination_categories(view, host_clutter_bics, link_model_bics,
                                        wallnoncore_bics, annotation_bics,
                                        override_bics, warnings, apply_fallback=apply_fallback)
        return view

    return template


# ─────────────────────────────────────────────────────────────────────────────
# DWG IMPORTS / TRAFFIC-LINK CATEGORY RESTRICTION  (element-level, view-specific)
# ─────────────────────────────────────────────────────────────────────────────

def _hide_dwg_imports_in_link(view, link_info, basement_view, warnings):
    link_doc = link_info[u"doc"]
    if link_doc is None:
        return
    try:
        imports = list(DB.FilteredElementCollector(link_doc).OfClass(DB.ImportInstance))
    except Exception as ex:
        warnings.append(u"Could not scan '{}' for imported DWG categories: {}".format(
            link_info[u"name"], ex))
        return
    if not imports:
        return

    to_hide = SCG.List[DB.ElementId]()
    for imp in imports:
        try:
            cat = imp.Category
            name = _elem_name(cat)
        except Exception:
            name = u""

        if basement_view and DWG_KEEP_TOKEN.upper() in (name or u"").upper():
            continue

        try:
            if imp.CanBeHidden(view):
                to_hide.Add(imp.Id)
        except Exception as ex:
            warnings.append(u"Could not evaluate DWG import '{}' in '{}': {}".format(
                name, link_info[u"name"], ex))

    if to_hide.Count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(
                u"Could not hide {} imported DWG object(s) inside '{}': {}".format(
                    to_hide.Count, link_info[u"name"], ex))


def _restrict_traffic_link_visibility(view, traffic_link, keep_bics, warnings):
    """Best-effort element-level hide — CONFIRMED UNRELIABLE for cross-
    document elements by live testing, not just suspected (see module
    docstring): a real run reported ~99% of candidate Model/Annotation
    elements as CanBeHidden()==False. This is why _apply_annotation_hide_
    filter/_apply_model_whitelist_filter (View Filters, applied on the
    template) exist as the ACTUAL working mechanism for hiding Traffic's
    non-Parking content — Filters reach linked elements the same way this
    tool's Structure/Architecture coloring already relies on, unlike
    element-level HideElements. This function is kept as a defensive,
    lower-priority second layer (harmless if it does nothing; free if a
    future Revit version happens to fix the underlying limitation) rather
    than the primary mechanism it originally was.

    Single pass over every instance in the link, grouped by each element's
    own resolved .Category (rather than one FilteredElementCollector pass
    per category via ElementCategoryFilter) -- fewer collector passes on a
    large civil/traffic model, and it can't disagree with an element about
    its own category. Restricted to CategoryType.Model/Annotation (see
    inline comment below) -- an earlier version counted EVERY non-type
    element in the document, including Materials/Phases/Views/Sheets/
    Schedules/etc (CategoryType.Internal) that were never going to be
    visible in a view at all, which drowned the real signal in ~2800
    expected, benign non-matches. Per-category counts are logged (pyRevit
    output) AND returned as a short summary string the caller can put
    directly in the final TaskDialog -- not just buried in the log -- so a
    test run gives a definite yes/no on whether View.HideElements actually
    took effect, checkable from the result dialog itself."""
    link_doc = traffic_link[u"doc"]
    if link_doc is None:
        warnings.append(u"Traffic link is unloaded — could not restrict its categories.")
        return u"Traffic link is unloaded — nothing was hidden."

    try:
        all_elems = list(DB.FilteredElementCollector(link_doc).WhereElementIsNotElementType())
    except Exception as ex:
        warnings.append(u"Could not scan elements in the Traffic link: {}".format(ex))
        return u"Could not scan the Traffic link's elements."

    to_hide = SCG.List[DB.ElementId]()
    per_category = {}   # category name -> [seen, hidden]
    no_category = 0
    non_graphical_count = 0   # skipped: CategoryType.Internal etc, see below
    candidate_count = 0   # non-kept Model/Annotation elements seen, before HideElements even runs

    for e in all_elems:
        try:
            cat = e.Category
        except Exception:
            cat = None
        if cat is None:
            no_category += 1
            continue

        # WhereElementIsNotElementType() collects EVERY non-type element in
        # the document, including plenty that were never going to be
        # visible in a view at all -- Materials, Phases, Views, Sheets,
        # Schedules, Project Information, Survey Point, Internal Origin,
        # Legend Components, etc. Those categories are CategoryType.Internal
        # (confirmed live-testing: a first pass reported 2784/2802 "could
        # not be hidden," but the overwhelming majority were exactly this
        # kind of non-graphical, non-placed element -- CanBeHidden is
        # correctly False for them because "hidden in view X" isn't a
        # meaningful concept for a Material definition or a Phase setting,
        # not because of any real limitation). Restricting to Model/
        # Annotation (the only CategoryTypes that can actually appear in a
        # view — matches the same distinction _apply_annotation_hide_filter/
        # _apply_model_whitelist_filter already use) makes the remaining
        # count and per-category breakdown mean something concrete instead
        # of being drowned in expected, benign non-matches.
        try:
            cat_type = cat.CategoryType
        except Exception:
            cat_type = None
        if cat_type not in (DB.CategoryType.Model, DB.CategoryType.Annotation):
            non_graphical_count += 1
            continue

        try:
            bic = DB.BuiltInCategory(cat.Id.IntegerValue)
        except Exception:
            bic = None

        cat_name = _elem_name(cat) or u"?"
        counts = per_category.setdefault(cat_name, [0, 0])
        counts[0] += 1

        if bic in keep_bics:
            continue
        candidate_count += 1

        try:
            if e.CanBeHidden(view):
                to_hide.Add(e.Id)
                counts[1] += 1
        except Exception:
            pass

    hidden_count = to_hide.Count
    if hidden_count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(
                u"Could not hide {} element(s) inside the Traffic link: {} "
                u"(View.HideElements may not support elements from a linked "
                u"document in this Revit version).".format(hidden_count, ex))
            hidden_count = 0
            for counts in per_category.values():
                counts[1] = 0

    breakdown = u", ".join(
        u"{}: {}/{} hidden".format(k, v[1], v[0]) for k, v in sorted(per_category.items()))
    try:
        logger.info(u"Traffic link — element visibility restriction: {} total hidden "
                    u"(kept categories: {}). {}{}{}".format(
                        hidden_count,
                        u", ".join(sorted(b.ToString() for b in keep_bics)),
                        breakdown or u"no elements found",
                        u"; {} element(s) with no category".format(no_category) if no_category else u"",
                        u"; {} non-graphical element(s) skipped (Materials/Phases/Views/"
                        u"Sheets/etc — CategoryType.Internal, never visible in a view "
                        u"regardless)".format(non_graphical_count) if non_graphical_count else u""))
    except Exception:
        pass

    unhidden_note = (u" — {} could not be hidden (CanBeHidden=False, an exception, or "
                      u"HideElements itself failed — see module docstring: this is a "
                      u"confirmed, not just suspected, cross-document HideElements "
                      u"limitation)".format(candidate_count - hidden_count)
                      if hidden_count < candidate_count else u"")
    return u"Traffic link: {} of {} non-kept Model/Annotation element(s) hidden ({}){}".format(
        hidden_count, candidate_count, breakdown or u"no elements found", unhidden_note)


def _set_traffic_halftone(view, traffic_link, warnings):
    ogs = DB.OverrideGraphicSettings()
    try:
        ogs.SetHalftone(True)
    except Exception as ex:
        warnings.append(u"Could not set the Traffic link to halftone: {}".format(ex))
        return
    try:
        view.SetElementOverrides(traffic_link[u"id"], ogs)
    except Exception as ex:
        warnings.append(u"Could not apply halftone to the Traffic link instance: {}".format(ex))


def _apply_link_display_settings(target, link_id, linked_view_id, warnings, label):
    """`target` must be whatever ensure_view_template() returned (the real
    template, or the fallback view when template setup failed) — NOT
    necessarily the active view. "V/G Overrides RVT Links" is itself a
    template-controlled parameter like every other V/G row; calling
    SetLinkOverrides on the plain active VIEW while a template still
    controls that row has no visible effect, since the template's own
    (uncontrolled, defaulted-to-ByHostView) value wins. Setting it directly
    on `target` is correct whether that's the template or the fallback view.

    BUG FIXED (found by reflecting the installed RevitAPI.dll directly):
    the enum for LinkVisibilityType's VALUE is a type called
    Autodesk.Revit.DB.LinkVisibility, NOT "LinkVisibilityType" — that name
    only exists as the *property* on RevitLinkGraphicsSettings, there is no
    top-level DB.LinkVisibilityType type at all.

    CONFIRMED (live exception, this round): Custom does NOT accept a
    non-invalid LinkedViewId — SetLinkOverrides throws "The LinkedViewId
    of linkDisplaySettings has incorrect value for the specified
    LinkVisibilityType." The property setters themselves don't validate
    this (confirmed via reflection — Custom + a fake ElementId sets
    without error at the property level), so it's enforced inside
    SetLinkOverrides itself, which needs a live View/link context outside
    reflection's reach — matches the user's exact error text. The only
    LinkVisibilityType that accepts a real LinkedViewId is ByLinkView —
    which, per Revit's own UI docs for the same Basics-tab "Linked view"
    picker, means the link displays exactly as that view displays,
    INCLUDING that view's own filters/overrides instead of the host's.
    That's a direct conflict with this tool's entire purpose for
    Architecture/Structure (the user's own original requirement was "so
    the host's View Filters still apply on top of the linked view's cut
    plane" — proven not simultaneously achievable via this API).

    UPDATE (see module docstring's TRAFFIC paragraph for the full history —
    this specific point flip-flopped twice): Architecture/Structure NEVER
    pass a real linked_view_id here — always None, always plain Custom.
    Traffic DOES pass one again (current state), when a Smart Linked View
    is chosen, ON THE CONDITION that the picked view is confirmed to have
    everything non-Parking hidden, including annotations, not just model
    content — otherwise ByLinkView's "renders independently of the host's
    settings entirely" reopens the exact dimensions-bleed-through bug this
    went through a full reversal over already. This asymmetry is
    deliberate: Traffic's own requirement (halftone + hide, no coloring)
    has no dependency on the host's Filters the way Architecture/Structure
    do, so trusting a well-prepared linked view is a legitimate tradeoff
    for Traffic specifically that it can never be for the other two roles.

    Best-effort regardless: RevitLinkGraphicsSettings/View.GetLinkOverrides
    was only added in the Revit 2024 API (this extension targets 2023+).
    This does NOT gate whether _restrict_traffic_link_visibility/
    _set_traffic_halftone/View Filters actually work: those are separate
    mechanisms from link display mode and apply regardless."""
    settings_cls = getattr(DB, "RevitLinkGraphicsSettings", None)
    if settings_cls is None:
        return
    vis_enum = getattr(DB, "LinkVisibility", None)
    if vis_enum is None:
        return
    try:
        link_settings = target.GetLinkOverrides(link_id)
        if link_settings is None:
            link_settings = settings_cls()
        if linked_view_id is not None:
            link_settings.LinkVisibilityType = vis_enum.ByLinkView
            link_settings.LinkedViewId = linked_view_id
        else:
            link_settings.LinkVisibilityType = vis_enum.Custom
        target.SetLinkOverrides(link_id, link_settings)
    except Exception as ex:
        warnings.append(
            u"Could not set the {} link's display mode (likely unavailable before "
            u"Revit 2024) — informational only, category visibility inside the link "
            u"was still applied directly: {}".format(label, ex))


def _ensure_linked_view_detail_level(chosen, warnings, label):
    """WARNING ONLY — do not attempt to modify the linked document. An
    earlier version of this function opened a Transaction directly against
    `link_doc` to force the chosen view's Detail Level to Fine; live
    testing hit a hard Revit exception: "Document is a linked file.
    Transactions can only be used in primary documents." That's Revit's
    own API restriction, unconditional — not a permissions/worksharing
    edge case that could be caught and retried, an outright ban on
    Transaction(link_doc, ...) for ANY document obtained via
    RevitLinkInstance.GetLinkDocument(), always. There is no way around it
    from a host-side script.

    RevitLinkGraphicsSettings.SetDetailLevel is also not callable — this
    has now been checked by reflecting the installed RevitAPI.dll THREE
    separate times across three rounds of this conversation, and every
    time the result is identical: Autodesk.Revit.DB.RevitLinkGraphicsSettings
    has exactly three members, full stop — IsValidObject, LinkVisibilityType,
    LinkedViewId. No DetailLevel property, no SetDetailLevel method, in
    this class, in this Revit version, ever. Calling it would raise an
    AttributeError immediately.

    Between "can't touch the linked document" and "the host-side settings
    object has no such property," there is genuinely no API path left to
    change a linked view's Detail Level from here. The only real fix is
    for the source model's owner to set that view's Detail Level to
    Medium/Fine in their own file — this function's only job is to make
    that actionable: name the exact link/view so the user knows what to
    ask for. _find_matching_link_views/_populate_linked_view_combo already
    sort Coarse candidates last and flag them directly in the combo label,
    so this should be uncommon to hit in the first place."""
    if chosen is None or not chosen.get(u"is_coarse"):
        return
    warnings.append(
        u"The {} linked view '{}' has Detail Level set to Coarse in its own file — "
        u"Revit may show that link's elements with their own hardcoded 'Coarse Scale "
        u"Fill Pattern' Type Property instead of this tool's red/blue hatch. There is no "
        u"way to override this from the host side (Revit forbids modifying a linked "
        u"document, and RevitLinkGraphicsSettings has no Detail Level property) — pick a "
        u"different linked view, or ask the source model's owner to set this view's "
        u"Detail Level to Medium/Fine.".format(label, chosen[u"name"]))


def _apply_smart_linked_view(chosen, warnings, label):
    """chosen: None, or {'view':, 'link':} from the wizard's linked-view
    combo for this role (Architecture or Structure — Traffic goes through
    _apply_link_display_settings directly at its own call site instead,
    since it doesn't have this function's conflict at all).

    DELIBERATELY DOES NOT apply the chosen view's cut plane/view range to
    the link. Confirmed via a live exception this round + reflection: the
    only Revit link-display mode that accepts a specific LinkedViewId
    (ByLinkView) also makes the link use THAT view's own filters instead
    of the host's — silently breaking this tool's red/blue View Filter
    coloring for whichever Architecture/Structure link it's applied to.
    Asked the user directly which tradeoff to take (Filters vs. matching
    cut plane); the answer was to always keep Filters correct. So this
    function only surfaces the Coarse-detail-level warning (see
    _ensure_linked_view_detail_level) and otherwise leaves the link on
    ByHostView, the default — View Filters keep applying to it without
    question, see module docstring. The chosen view still has real value:
    it identified the right level/view for reference, and its own Detail
    Level was checked; it's just never pushed into RevitLinkGraphicsSettings
    for this role."""
    if chosen is None:
        return
    _ensure_linked_view_detail_level(chosen, warnings, label)


# ─────────────────────────────────────────────────────────────────────────────
# GRIDS  (Refinement 1)
# ─────────────────────────────────────────────────────────────────────────────

def _hide_host_grids(view, warnings):
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    if grids_bic is None:
        return
    try:
        host_grids = (DB.FilteredElementCollector(doc)
                        .OfCategory(grids_bic)
                        .WhereElementIsNotElementType()
                        .ToElements())
    except Exception as ex:
        warnings.append(u"Could not collect host Grids: {}".format(ex))
        return

    to_hide = SCG.List[DB.ElementId]()
    for g in host_grids:
        try:
            if g.CanBeHidden(view) and not g.IsHidden(view):
                to_hide.Add(g.Id)
        except Exception:
            pass

    if to_hide.Count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(u"Could not hide {} host Grid(s): {}".format(to_hide.Count, ex))


def _color_link_grids(view, link_info, ogs, warnings, label):
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    link_doc = link_info[u"doc"]
    if grids_bic is None or link_doc is None:
        return
    try:
        grids = (DB.FilteredElementCollector(link_doc)
                   .OfCategory(grids_bic)
                   .WhereElementIsNotElementType()
                   .ToElements())
    except Exception:
        return

    total  = 0
    failed = 0
    for g in grids:
        total += 1
        try:
            view.SetElementOverrides(g.Id, ogs)
        except Exception:
            failed += 1

    if total == 0:
        return
    if failed == total:
        warnings.append(
            u"Could not color any Grid inside '{}' — Revit rejected every attempt "
            u"(View.SetElementOverrides may not support elements from a linked "
            u"document in this Revit version).".format(link_info[u"name"]))
    elif failed:
        warnings.append(u"{} of {} Grids inside '{}' could not be colored.".format(
            failed, total, link_info[u"name"]))


# ─────────────────────────────────────────────────────────────────────────────
# DEEP-SCAN CONCRETE CLASSIFICATION  (Step 8B)
# ─────────────────────────────────────────────────────────────────────────────

def _bip_text(elem, bip_name):
    bip = getattr(DB.BuiltInParameter, bip_name, None)
    if bip is None:
        return u""
    try:
        p = elem.get_Parameter(bip)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def _named_text(elem, display_name):
    try:
        p = elem.LookupParameter(display_name)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def _first_text(elem, bip_name, display_name):
    return _bip_text(elem, bip_name) or _named_text(elem, display_name)


_WORD_BOUNDARY_PATTERN_CACHE = {}


def _contains_any(haystack, keywords):
    """Word-boundary match, not raw substring — a keyword must appear as its
    own token, not merely as a fragment of an unrelated longer word. Matters
    because short/generic keywords are exactly the kind a team ends up
    adding (e.g. "Con" as shorthand for concrete) and a raw substring check
    can't tell "Con" matching "Concrete" apart from "Con" matching
    "Continuous"/"Contact"/"Connection".

    BUG FIXED (found live-testing "30x250"/family name
    "Concrete_Rectangular_Two side Rounded_Column" — it wasn't matching
    "Concrete" despite the keyword being present verbatim): Python's \\b/\\w
    treat underscore as a WORD character, so \\bCONCRETE\\b does not match
    inside "CONCRETE_RECTANGULAR_COLUMN" — there's no boundary between "E"
    and "_", both count as \\w. That's exactly Revit's own default
    underscore-delimited family/type naming convention, so the original
    word-boundary fix (for the "Con"-inside-"Continuous" false-positive)
    was quietly breaking the far more common case. Fixed with an explicit
    boundary definition instead of \\b/\\w: only [0-9A-Za-z<Hebrew>] count as
    "word" characters here, so underscore/hyphen/space/period/parentheses
    all correctly act as separators on both sides, while plain letters
    still don't (so "Con" still won't match inside "Continuous")."""
    if not haystack:
        return False
    up = haystack.upper()
    for k in (keywords or []):
        if not k:
            continue
        ku = k.upper()
        pattern = _WORD_BOUNDARY_PATTERN_CACHE.get(ku)
        if pattern is None:
            pattern = re.compile(
                u"(?<![0-9A-Za-zא-ת])" + re.escape(ku) + u"(?![0-9A-Za-zא-ת])")
            _WORD_BOUNDARY_PATTERN_CACHE[ku] = pattern
        if pattern.search(up):
            return True
    return False


def _material_texts(link_doc, material_id):
    """(name, material_class) for one material — MaterialClass matters
    because a real-world concrete material is very often NAMED after its
    grade only (e.g. "B30", a standard Israeli/European concrete-strength
    designation with no "concrete" substring at all), while Revit's own
    Material Browser classification for a properly set-up concrete material
    is reliably "Concrete" (or "Concrete, Cast-in-Place"/"Concrete,
    Precast", still containing "Concrete") regardless of what it's actually
    named. Returned as a pair, not a merged list, because _type_is_concrete
    checks material_class on its own as an unconditional signal (bypasses
    ExcludeKeywords entirely) on top of feeding the same name text into the
    general keyword-matching pool everything else uses."""
    if material_id is None or material_id == DB.ElementId.InvalidElementId:
        return (u"", u"")
    try:
        mat = link_doc.GetElement(material_id)
        if mat is None:
            return (u"", u"")
    except Exception:
        return (u"", u"")
    name = _elem_name(mat) or u""
    try:
        cls = mat.MaterialClass or u""
    except Exception:
        cls = u""
    return (name, cls)


def _wall_core_material_texts(link_doc, wall_type):
    """(texts, material_classes) — texts feed the general keyword pool,
    material_classes is checked unconditionally in _type_is_concrete."""
    texts = []
    classes = []
    try:
        cs = wall_type.GetCompoundStructure()
        if cs is None:
            return texts, classes
        first = cs.GetFirstCoreLayerIndex()
        last  = cs.GetLastCoreLayerIndex()
        layers = list(cs.GetLayers())
        for i, layer in enumerate(layers):
            if first <= i <= last:
                name, cls = _material_texts(link_doc, layer.MaterialId)
                if name:
                    texts.append(name)
                if cls:
                    classes.append(cls)
    except Exception:
        pass
    return texts, classes


def _structural_material_texts(link_doc, elem_type):
    """(texts, material_classes) — see _wall_core_material_texts."""
    bip = getattr(DB.BuiltInParameter, "STRUCTURAL_MATERIAL_PARAM", None)
    if bip is None:
        return [], []
    try:
        p = elem_type.get_Parameter(bip)
        if p is not None:
            name, cls = _material_texts(link_doc, p.AsElementId())
            return ([name] if name else []), ([cls] if cls else [])
    except Exception:
        pass
    return [], []


def _instance_material_texts(link_doc, elem):
    """Per-INSTANCE 'Material' parameter (MATERIAL_ID_PARAM) — some families
    expose a per-instance material override distinct from the type's
    Structural Material default, e.g. a framing/column instance manually
    swapped to a specific concrete without a matching dedicated type. Type
    classification is cached per type-id for speed (see
    _collect_concrete_type_names); this is checked per-instance as a
    supplementary signal precisely because it CAN vary between instances of
    the same type in a way the type-level cache wouldn't otherwise catch.
    Flat text list (name + class merged) — this feeds the general keyword
    pool only, unlike the type-level checks which also test material_class
    unconditionally."""
    bip = getattr(DB.BuiltInParameter, "MATERIAL_ID_PARAM", None)
    if bip is None:
        return []
    try:
        p = elem.get_Parameter(bip)
        if p is not None:
            name, cls = _material_texts(link_doc, p.AsElementId())
            return [t for t in (name, cls) if t]
    except Exception:
        pass
    return []


def _type_is_concrete(link_doc, elem_type, bic, cfg):
    concrete_kw = cfg.get(u"ConcreteKeywords") or []
    exclude_kw  = cfg.get(u"ExcludeKeywords") or []

    type_name = _elem_name(elem_type)

    # Manual, per-Type-Name overrides — set via "ARC/STR Settings" > one of
    # the two "Add from Model..." pickers (pick a rogue linked element
    # instead of typing a name blind). Checked FIRST and unconditionally,
    # ahead of even the MaterialClass fast-path below: this is a
    # deliberate human decision about ONE specific type that keeps getting
    # misclassified (in either direction), so it must win over every
    # automatic rule, not just the keyword ones. ManualIncludeTypeNames is
    # checked first, so it wins if a name somehow ends up in both lists.
    manual_include = cfg.get(u"ManualIncludeTypeNames") or []
    if type_name in manual_include:
        return True
    manual_exclude = cfg.get(u"ManualExcludeTypeNames") or []
    if type_name in manual_exclude:
        return False

    texts = [type_name]
    texts.append(_first_text(elem_type, "ALL_MODEL_DESCRIPTION", u"Description"))
    texts.append(_first_text(elem_type, "ALL_MODEL_TYPE_COMMENTS", u"Type Comments"))
    # Family Name — walls are a system family (no .Family the same way loaded
    # families have one), so this only ever contributes for the loadable-
    # family categories (columns/framing/foundations); harmless no-op for walls.
    try:
        fam = getattr(elem_type, "Family", None)
        if fam is not None:
            texts.append(_elem_name(fam))
    except Exception:
        pass

    # OST_Columns / OST_StructuralColumns both fall into the "else" branch
    # here (only Walls use compound-structure core layers) — Structural
    # Material + its MaterialClass is the signal for columns/framing/
    # foundations, same as for any other non-wall structural category.
    if bic == DB.BuiltInCategory.OST_Walls:
        mat_texts, mat_classes = _wall_core_material_texts(link_doc, elem_type)
    else:
        mat_texts, mat_classes = _structural_material_texts(link_doc, elem_type)
    texts.extend(mat_texts)

    # Revit's own Material Browser "Class" is an authoritative signal,
    # independent of whatever the material/type/family happens to be named
    # -- e.g. a material literally named "B30" (a grade code, no "concrete"
    # substring at all) with Class correctly set to "Concrete". Checked
    # FIRST and unconditionally — bypasses ExcludeKeywords entirely — per
    # live-testing feedback: Revit's own classification should win over a
    # team's text-keyword heuristic, not lose to it on an unlucky Exclude
    # match elsewhere in the same element's text.
    if _contains_any(u" | ".join(mat_classes), [u"Concrete", u"בטון"]):
        return True

    combined = u" | ".join(t for t in texts if t)
    if not _contains_any(combined, concrete_kw):
        return False
    if _contains_any(combined, exclude_kw):
        return False
    return True


def _type_ifc_guid(elem_type):
    """Type IfcGUID (BuiltInParameter.IFC_TYPE_GUID) as a string, or None.
    Confirmed via reflecting the installed RevitAPI.dll that this
    BuiltInParameter is real, and confirmed EMPIRICALLY (2026-08-30) that
    its value differs between two identically-NAMED type elements in two
    different linked documents — Revit derives it from the type's own
    persistent identity, unlike a Type Name, which a modeler can name
    however they like with zero cross-file uniqueness guarantee. Used only
    to disambiguate a Type Name shared by both the Architecture and
    Structure links — see run()'s ambiguous_names handling."""
    try:
        bip = getattr(DB.BuiltInParameter, "IFC_TYPE_GUID", None)
        if bip is None:
            return None
        p = elem_type.get_Parameter(bip)
        if p is None or not p.HasValue:
            return None
        return p.AsString() or None
    except Exception:
        return None


def _collect_all_type_names(link_doc, categories, warnings, label, _depth=0):
    """Every Type Name found in `categories` for this link, no material/
    keyword classification at all — used for Architecture's Section-only
    filter (FILTER_NAME_ARCH_SECTION), which treats every type in its wider
    category scope (SECTION_ARCH_CATEGORY_NAMES) identically regardless of
    whether it's concrete, so there's no need for _collect_concrete_type_
    names' classification machinery here. Same nested-link recursion (capped
    at one level) as that function, for consistency."""
    names = set()
    if link_doc is None:
        return names
    for bic in categories:
        try:
            elems = (DB.FilteredElementCollector(link_doc)
                       .OfCategory(bic)
                       .WhereElementIsNotElementType()
                       .ToElements())
        except Exception:
            continue
        for elem in elems:
            try:
                type_id = elem.GetTypeId()
            except Exception:
                continue
            if type_id is None or type_id == DB.ElementId.InvalidElementId:
                continue
            try:
                elem_type = link_doc.GetElement(type_id)
            except Exception:
                elem_type = None
            type_name = _elem_name(elem_type) if elem_type is not None else None
            if type_name:
                names.add(type_name)

    if _depth < 1:
        try:
            nested = list(DB.FilteredElementCollector(link_doc).OfClass(DB.RevitLinkInstance))
        except Exception:
            nested = []
        for ninst in nested:
            try:
                ndoc = ninst.GetLinkDocument()
            except Exception:
                ndoc = None
            if ndoc is None:
                continue
            names |= _collect_all_type_names(
                ndoc, categories, warnings, u"{} > nested link".format(label), _depth=_depth + 1)

    return names


def _collect_concrete_type_names(link_doc, categories, cfg, warnings, label, _depth=0,
                                  guids_by_name=None, all_names_out=None):
    """Iterate INSTANCES (not just types) because an instance-level Material
    override (_instance_material_texts) can vary between instances of the
    same type — a type only counts as concrete via the type cache if at
    least one qualifying instance uses it, but any single instance whose
    own Material override reads as concrete adds its type name too, even if
    the type's own default classification came back negative.

    BUG FOUND AND FIXED (root-caused precisely via live testing — a wall
    named "50_CONC" with a Type Properties compound-structure core layer
    literally named "Concrete" was still not being classified as concrete,
    while "30_CONC"/"40_CONC" were): an earlier version of this loop
    skipped any WALL instance whose Structural Usage wasn't Bearing or
    Shear, BEFORE _type_is_concrete was ever called on it — "50_CONC" was
    Non-bearing, so it was silently discarded regardless of its actual
    material. That check conflated two unrelated things: whether a wall is
    MATERIALLY concrete (what this tool colors) and whether it's
    STRUCTURALLY load-bearing (an unrelated property — plenty of walls are
    architecturally modeled in concrete without being marked Bearing/Shear
    at all, e.g. non-load-bearing concrete partitions or facade panels).
    Removed entirely; walls are now classified purely on
    name/material, same as every other category here.

    Recurses one level into any RevitLinkInstance found inside `link_doc`
    itself (a link nested inside the Arch/Struct link, e.g. a separately-
    linked rebar/precast file within the Structure model) — capped at one
    level to bound the work and because Revit doesn't allow circular links
    so a small fixed cap is enough of a safety margin regardless.

    Logs a per-category scanned/matched count via the pyRevit logger (not
    `warnings` — this is routine diagnostic info, not something wrong) so
    that if an element is ever missing from the coloring despite looking
    like it should qualify, the pyRevit output window immediately shows
    whether it's a classification gap (matched count too low) or a filter-
    matching gap downstream (classification looked right, element still
    didn't get colored).

    all_names_out (Sections only, see SECTION_TEMPLATE_NAME's docstring):
    if given, gets every Type Name encountered in `categories` added to
    it, concrete or not — one entry per distinct type, added the same
    place is_concrete gets cached (once per type_id, not once per
    instance). Subtracting the returned concrete-only `names` from this
    gives each link's own NON-concrete type names, used to build the
    Section-only fallback filters instead of the plan template's blanket
    category override."""
    names = set()
    type_cache = {}  # type_id (int) -> (is_concrete, type_name, ifc_guid)
    per_category = {}  # category display name -> [scanned, matched]

    if link_doc is None:
        return names

    concrete_kw = cfg.get(u"ConcreteKeywords") or []
    exclude_kw  = cfg.get(u"ExcludeKeywords") or []

    for bic in categories:
        try:
            elems = (DB.FilteredElementCollector(link_doc)
                       .OfCategory(bic)
                       .WhereElementIsNotElementType()
                       .ToElements())
        except Exception:
            continue

        cat_key = bic.ToString()
        counts = per_category.setdefault(cat_key, [0, 0])
        is_wall = (bic == DB.BuiltInCategory.OST_Walls)

        for elem in elems:
            counts[0] += 1

            try:
                type_id = elem.GetTypeId()
            except Exception:
                continue
            if type_id is None or type_id == DB.ElementId.InvalidElementId:
                continue

            key = type_id.IntegerValue
            if key not in type_cache:
                elem_type = link_doc.GetElement(type_id)
                is_conc = False
                type_name = None
                ifc_guid = None
                if elem_type is not None:
                    type_name = _elem_name(elem_type)
                    try:
                        is_conc = _type_is_concrete(link_doc, elem_type, bic, cfg)
                    except Exception as ex:
                        warnings.append(u"{}: could not classify type '{}': {}".format(
                            label, type_name, ex))
                    if guids_by_name is not None:
                        ifc_guid = _type_ifc_guid(elem_type)
                    if all_names_out is not None and type_name:
                        all_names_out.add(type_name)
                type_cache[key] = (is_conc, type_name, ifc_guid)

            is_conc, type_name, ifc_guid = type_cache[key]

            if not is_conc and type_name and not is_wall:
                try:
                    inst_texts = _instance_material_texts(link_doc, elem)
                    if inst_texts:
                        combined = u" | ".join(inst_texts)
                        if (_contains_any(combined, concrete_kw)
                                and not _contains_any(combined, exclude_kw)):
                            is_conc = True
                except Exception:
                    pass

            if is_conc and type_name:
                names.add(type_name)
                counts[1] += 1
                if guids_by_name is not None and ifc_guid:
                    guids_by_name.setdefault(type_name, set()).add(ifc_guid)

    try:
        breakdown = u", ".join(
            u"{}: {}/{} matched".format(k, v[1], v[0]) for k, v in sorted(per_category.items()))
        logger.info(u"{}: concrete deep-scan — {}".format(label, breakdown or u"no elements found"))
    except Exception:
        pass

    if _depth < 1:
        try:
            nested = list(DB.FilteredElementCollector(link_doc).OfClass(DB.RevitLinkInstance))
        except Exception:
            nested = []
        for ninst in nested:
            try:
                ndoc = ninst.GetLinkDocument()
            except Exception:
                ndoc = None
            if ndoc is None:
                continue
            names |= _collect_concrete_type_names(
                ndoc, categories, cfg, warnings,
                u"{} > nested link".format(label), _depth=_depth + 1,
                guids_by_name=guids_by_name, all_names_out=all_names_out)

    return names


# ─────────────────────────────────────────────────────────────────────────────
# 2D MISMATCH DETECTION + BCF EXPORT
# ─────────────────────────────────────────────────────────────────────────────

MISMATCH_2D_TOLERANCE_FT = 0.5
# ~150mm default — a Structure/Architecture concrete footprint pair within
# this in every direction is treated as "the same element coordinated
# correctly"; anything wider is flagged. Passed as detect_2d_mismatches'
# tolerance_ft, not hardcoded there, so a future Settings.json key can
# override it without touching this function.


def get_concrete_elements(link_doc, categories, cfg):
    """Same classification signals as _collect_concrete_type_names (type-
    level _type_is_concrete, including its manual include/exclude and
    Material Class checks, plus the per-instance _instance_material_texts
    override) so mismatch detection only ever flags/matches elements that
    would ALSO get colored by the visual hatch -- a "mismatch" that isn't
    even classified as concrete would be meaningless noise. Returns actual
    Element objects (not just Type Names, unlike _collect_concrete_type_
    names) since detect_2d_mismatches needs each instance's own geometry.
    Deliberately NOT calling _collect_concrete_type_names itself and
    reshaping its result -- that function is load-bearing, live-tested
    production logic (see its own docstring's bug-fix history) collecting
    names into a set; duplicating its per-element loop here instead of
    changing its signature/return shape keeps that function's proven
    behavior completely untouched. No nested-link recursion (unlike
    _collect_concrete_type_names) -- kept simple for this first version;
    add it the same way (one level, capped) if nested-link content turns
    out to need mismatch checking too."""
    elements = []
    if link_doc is None:
        return elements

    type_cache = {}  # type_id (int) -> is_concrete
    concrete_kw = cfg.get(u"ConcreteKeywords") or []
    exclude_kw  = cfg.get(u"ExcludeKeywords") or []

    for bic in categories:
        try:
            elems = (DB.FilteredElementCollector(link_doc)
                       .OfCategory(bic)
                       .WhereElementIsNotElementType()
                       .ToElements())
        except Exception:
            continue
        is_wall = (bic == DB.BuiltInCategory.OST_Walls)

        for elem in elems:
            try:
                type_id = elem.GetTypeId()
            except Exception:
                continue
            if type_id is None or type_id == DB.ElementId.InvalidElementId:
                continue

            key = type_id.IntegerValue
            if key not in type_cache:
                elem_type = link_doc.GetElement(type_id)
                is_conc = False
                if elem_type is not None:
                    try:
                        is_conc = _type_is_concrete(link_doc, elem_type, bic, cfg)
                    except Exception:
                        is_conc = False
                type_cache[key] = is_conc
            is_conc = type_cache[key]

            if not is_conc and not is_wall:
                try:
                    inst_texts = _instance_material_texts(link_doc, elem)
                    if inst_texts:
                        combined = u" | ".join(inst_texts)
                        if (_contains_any(combined, concrete_kw)
                                and not _contains_any(combined, exclude_kw)):
                            is_conc = True
                except Exception:
                    pass

            if is_conc:
                elements.append(elem)

    return elements


def _transformed_2d_bbox(elem, transform):
    """(min_x, min_y, max_x, max_y) of elem's BoundingBox in HOST document
    coordinates, or None if it has none. Transforms all 8 corners (not just
    Min/Max) before taking the axis-aligned min/max -- a link rotated
    relative to the host would otherwise give a wrong, un-rotated box if
    only the two corner points were transformed directly."""
    try:
        bbox = elem.get_BoundingBox(None)
    except Exception:
        bbox = None
    if bbox is None:
        return None
    mn, mx = bbox.Min, bbox.Max
    corners = [
        DB.XYZ(mn.X, mn.Y, mn.Z), DB.XYZ(mx.X, mn.Y, mn.Z),
        DB.XYZ(mn.X, mx.Y, mn.Z), DB.XYZ(mx.X, mx.Y, mn.Z),
        DB.XYZ(mn.X, mn.Y, mx.Z), DB.XYZ(mx.X, mn.Y, mx.Z),
        DB.XYZ(mn.X, mx.Y, mx.Z), DB.XYZ(mx.X, mx.Y, mx.Z),
    ]
    try:
        pts = [transform.OfPoint(c) for c in corners]
    except Exception:
        return None
    xs = [p.X for p in pts]
    ys = [p.Y for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def detect_2d_mismatches(doc, arch_link, struct_link, tolerance_ft, override_bics, settings):
    """For every concrete Structure element (get_concrete_elements on
    struct_link), find the best-overlapping concrete Architecture element
    (get_concrete_elements on arch_link) within tolerance_ft of each other
    in plan (host-coordinate 2D footprint, via _transformed_2d_bbox) and
    flag an issue dict if either (a) no Architecture element overlaps it
    at all within tolerance, or (b) one does, but its footprint still
    differs from Structure's by more than tolerance_ft in some direction
    (e.g. same column grid position, different size/edge). "Best" match
    = largest 2D overlap area, so a nearby-but-unrelated element doesn't
    win over the one actually coordinating with this Structure element.
    Only Structure elements are walked (not the reverse) -- an
    Architecture element with no Structure counterpart at all (e.g. a
    partition wall) isn't a structural coordination issue."""
    issues = []
    arch_doc    = arch_link.get(u"doc")
    struct_doc  = struct_link.get(u"doc")
    arch_inst   = arch_link.get(u"instance")
    struct_inst = struct_link.get(u"instance")
    if arch_doc is None or struct_doc is None or arch_inst is None or struct_inst is None:
        return issues

    try:
        arch_transform = arch_inst.GetTotalTransform()
        struct_transform = struct_inst.GetTotalTransform()
    except Exception:
        return issues

    arch_boxes = []
    for elem in get_concrete_elements(arch_doc, override_bics, settings):
        box = _transformed_2d_bbox(elem, arch_transform)
        if box is not None:
            arch_boxes.append((elem, box))

    for struct_elem in get_concrete_elements(struct_doc, override_bics, settings):
        struct_box = _transformed_2d_bbox(struct_elem, struct_transform)
        if struct_box is None:
            continue
        smin_x, smin_y, smax_x, smax_y = struct_box

        best_arch, best_box, best_area = None, None, -1.0
        for arch_elem, arch_box in arch_boxes:
            amin_x, amin_y, amax_x, amax_y = arch_box
            if (amax_x + tolerance_ft < smin_x or smax_x + tolerance_ft < amin_x or
                    amax_y + tolerance_ft < smin_y or smax_y + tolerance_ft < amin_y):
                continue  # not even within tolerance of overlapping — not a candidate at all
            ox = min(amax_x, smax_x) - max(amin_x, smin_x)
            oy = min(amax_y, smax_y) - max(amin_y, smin_y)
            area = max(ox, 0.0) * max(oy, 0.0)
            if area > best_area:
                best_arch, best_box, best_area = arch_elem, arch_box, area

        center = ((smin_x + smax_x) / 2.0, (smin_y + smax_y) / 2.0)

        if best_arch is None:
            issues.append({
                u"type": u"missing_architecture",
                u"struct_elem": struct_elem, u"arch_elem": None,
                u"struct_box": struct_box, u"arch_box": None, u"center": center,
                u"title": u"Structure element with no matching Architecture element",
                u"description": (
                    u"Structure element Id {} (Category: {}) has no concrete Architecture "
                    u"element within {:.2f} ft of it.".format(
                        struct_elem.Id.IntegerValue,
                        _elem_name(struct_elem.Category) if struct_elem.Category else u"?",
                        tolerance_ft)),
            })
            continue

        amin_x, amin_y, amax_x, amax_y = best_box
        delta = max(abs(smin_x - amin_x), abs(smin_y - amin_y),
                    abs(smax_x - amax_x), abs(smax_y - amax_y))
        if delta > tolerance_ft:
            issues.append({
                u"type": u"footprint_mismatch",
                u"struct_elem": struct_elem, u"arch_elem": best_arch,
                u"struct_box": struct_box, u"arch_box": best_box, u"center": center,
                u"title": u"Structure/Architecture footprint mismatch",
                u"description": (
                    u"Structure element Id {} and Architecture element Id {} overlap, but "
                    u"their 2D footprints differ by {:.2f} ft in at least one direction "
                    u"(tolerance {:.2f} ft).".format(
                        struct_elem.Id.IntegerValue, best_arch.Id.IntegerValue,
                        delta, tolerance_ft)),
            })

    return issues


def export_mismatches_to_bcf(mismatches, save_path):
    """Minimal, best-effort BCF 2.1 export — one Topic per mismatch, zipped
    per the BCF 2.1 folder layout (bcf.version + one GUID folder per topic
    holding markup.bcf + viewpoint.bcfv). NOT validated against the
    official BCF 2.1 XSD or a real BCF reader/ACC import — in particular,
    <SheetName> is NOT part of the standard BCF 2.1 schema at all; it's
    included only because ACC/BIM 360 is reported to use it to place a 2D
    pushpin on a specific sheet. Confirm a real import actually does that
    before relying on this for anything more than a rough visual record."""
    BCF_NS = u"http://www.buildingsmart-tech.org/specifications/BCF-XML"
    now_iso = datetime.datetime.utcnow().strftime(u"%Y-%m-%dT%H:%M:%SZ")

    zf = zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED)
    try:
        version_root = ET.Element(u"Version", {u"xmlns": BCF_NS, u"VersionId": u"2.1"})
        zf.writestr(u"bcf.version", ET.tostring(version_root, encoding=u"utf-8"))

        for issue in mismatches:
            guid = str(uuid.uuid4())
            view_name = issue.get(u"view_name")
            sheet_number = issue.get(u"sheet_number")
            sheet_or_view = sheet_number or view_name

            markup = ET.Element(u"Markup", {u"xmlns": BCF_NS})
            topic = ET.SubElement(markup, u"Topic", {
                u"Guid": guid, u"TopicType": u"Clash", u"TopicStatus": u"Open"})
            ET.SubElement(topic, u"Title").text = issue.get(u"title") or u"2D Mismatch"
            ET.SubElement(topic, u"Description").text = issue.get(u"description") or u""
            ET.SubElement(topic, u"CreationDate").text = now_iso
            ET.SubElement(topic, u"CreationAuthor").text = u"EasyBIM Coordination Graphics"
            if sheet_or_view:
                ET.SubElement(topic, u"SheetName").text = sheet_or_view
            zf.writestr(u"{}/markup.bcf".format(guid), ET.tostring(markup, encoding=u"utf-8"))

            center = issue.get(u"center") or (0.0, 0.0)
            viz = ET.Element(u"VisualizationInfo", {u"xmlns": BCF_NS, u"Guid": guid})
            cam = ET.SubElement(viz, u"OrthogonalCamera")
            view_point = ET.SubElement(cam, u"CameraViewPoint")
            ET.SubElement(view_point, u"X").text = str(center[0])
            ET.SubElement(view_point, u"Y").text = str(center[1])
            ET.SubElement(view_point, u"Z").text = u"0"
            direction = ET.SubElement(cam, u"CameraDirection")
            ET.SubElement(direction, u"X").text = u"0"
            ET.SubElement(direction, u"Y").text = u"0"
            ET.SubElement(direction, u"Z").text = u"-1"
            up_vec = ET.SubElement(cam, u"CameraUpVector")
            ET.SubElement(up_vec, u"X").text = u"0"
            ET.SubElement(up_vec, u"Y").text = u"1"
            ET.SubElement(up_vec, u"Z").text = u"0"
            ET.SubElement(cam, u"ViewToWorldScale").text = u"1"
            if sheet_or_view:
                ET.SubElement(viz, u"Document").text = sheet_or_view
            zf.writestr(u"{}/viewpoint.bcfv".format(guid), ET.tostring(viz, encoding=u"utf-8"))
    finally:
        zf.close()


# ─────────────────────────────────────────────────────────────────────────────
# VIEW FILTERS  (Step 8C/8D)
# ─────────────────────────────────────────────────────────────────────────────

def _find_existing_filter(name):
    for f in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement):
        if _elem_name(f) == name:
            return f
    return None


def _build_and_hide_by_categories(target, filter_name, cat_ids, warnings):
    """Bulletproof, View-Filter-based category hide. A ParameterFilterElement
    scoped to `cat_ids` with NO ElementFilter rule set matches every element
    in those categories (confirmed via reflecting the installed RevitAPI.dll:
    ParameterFilterElement.Create has a real 3-argument overload — Document,
    name, categories — with no ElementFilter parameter at all, so this isn't
    a workaround, it's a directly-supported creation mode). Applied via
    View.SetFilterVisibility(id, False) — a real, distinct method from
    SetFilterOverrides (confirmed via reflection), the API for the
    "Visibility" checkbox in the Filters tab of V/G Overrides, genuinely
    suppressing matched elements rather than just recoloring them.

    View Filters reach linked elements the same way this tool's Structure/
    Architecture coloring filters already do (Step 8's whole premise) — this
    is why this mechanism was reached for after both SetCategoryHidden (on
    the template) and RevitLinkGraphicsSettings (which has no category
    control at all) hit real limitations."""
    if not cat_ids:
        return
    net_cat_ids = SCG.List[DB.ElementId](cat_ids)
    pfe = _find_existing_filter(filter_name)
    try:
        if pfe is None:
            pfe = DB.ParameterFilterElement.Create(doc, filter_name, net_cat_ids)
        else:
            pfe.SetCategories(net_cat_ids)
    except Exception as ex:
        warnings.append(u"Could not create/update view filter '{}': {}".format(filter_name, ex))
        return
    try:
        applied_ids = [i.IntegerValue for i in target.GetFilters()]
        if pfe.Id.IntegerValue not in applied_ids:
            target.AddFilter(pfe.Id)
        target.SetFilterVisibility(pfe.Id, False)
    except Exception as ex:
        warnings.append(u"Could not apply view filter '{}': {}".format(filter_name, ex))


def _apply_annotation_hide_filter(target, warnings):
    """Every Annotation category except ANNOTATION_HIDE_KEEP_VISIBLE (Room
    Tags). Category.get_AllowsVisibilityControl checked per category to
    skip (not warn about — matches _hide_category_safe's own reasoning)
    the ones Revit itself says can't be controlled here, same as every
    other category-touching function in this file."""
    keep_names = set(ANNOTATION_HIDE_KEEP_VISIBLE)
    cat_ids = []
    for cat in doc.Settings.Categories:
        try:
            if cat.CategoryType != DB.CategoryType.Annotation:
                continue
            try:
                bic_name = DB.BuiltInCategory(cat.Id.IntegerValue).ToString()
            except Exception:
                bic_name = None
            if bic_name in keep_names:
                continue
            if not cat.get_AllowsVisibilityControl(target):
                continue
            cat_ids.append(cat.Id)
        except Exception:
            continue
    _build_and_hide_by_categories(target, FILTER_NAME_HIDE_ANNOTATIONS, cat_ids, warnings)


def _apply_model_whitelist_filter(target, warnings):
    """Every Model category except WHITELIST_MODEL_CATEGORY_NAMES."""
    keep_bics = set(_resolve_categories(WHITELIST_MODEL_CATEGORY_NAMES, warnings))
    cat_ids = []
    for cat in doc.Settings.Categories:
        try:
            if cat.CategoryType != DB.CategoryType.Model:
                continue
            try:
                bic = DB.BuiltInCategory(cat.Id.IntegerValue)
            except Exception:
                bic = None
            if bic is not None and bic in keep_bics:
                continue
            if not cat.get_AllowsVisibilityControl(target):
                continue
            cat_ids.append(cat.Id)
        except Exception:
            continue
    _build_and_hide_by_categories(target, FILTER_NAME_WHITELIST_MODEL, cat_ids, warnings)


_STALE_COLUMN_FILTER_NAMES = (
    u"EasyBIM - Structure Concrete Columns",
    u"EasyBIM - Architecture Concrete Columns",
)


def _cleanup_stale_column_filters(warnings):
    """Removes the two column-specific filters a previous run may have
    created before the dashed-column treatment was reversed (see
    OVERRIDE_CATEGORY_NAMES above) — deleting a ParameterFilterElement
    also removes its association from any view/template using it, so
    there's nothing to unapply from `template` first."""
    for name in _STALE_COLUMN_FILTER_NAMES:
        pfe = _find_existing_filter(name)
        if pfe is None:
            continue
        try:
            doc.Delete(pfe.Id)
        except Exception as ex:
            warnings.append(u"Could not remove the stale filter '{}': {}".format(name, ex))


_STALE_WHITELIST_FILTER_NAMES = (
    FILTER_NAME_HIDE_ANNOTATIONS,
    FILTER_NAME_WHITELIST_MODEL,
)


def _cleanup_stale_whitelist_filters(warnings):
    """URGENT — the View-Filter-based category whitelist (a
    ParameterFilterElement created with NO ElementFilter rule at all, an
    unusual configuration a normal user would never produce through
    Revit's own UI) is CONFIRMED to crash Revit outright when the user
    opens the Filters tab of a View/View Template Properties dialog —
    live-tested, a hard application crash, not a catchable exception.
    Removes both filters this tool ever created that way, same pattern as
    _cleanup_stale_column_filters — deleting a ParameterFilterElement
    removes its association from any view/template using it too, so
    there's nothing to unapply from `target` first. Do not recreate these
    filters (see _apply_coordination_categories — the calls that used to
    build them are removed, not just commented out) without confirming
    what specifically about a no-rule filter crashes Revit's UI, and a
    real fix, first."""
    for name in _STALE_WHITELIST_FILTER_NAMES:
        pfe = _find_existing_filter(name)
        if pfe is None:
            continue
        try:
            doc.Delete(pfe.Id)
        except Exception as ex:
            warnings.append(u"Could not remove the stale filter '{}': {}".format(name, ex))


def build_or_update_type_name_filter(filter_name, category_bics, type_names, warnings,
                                      guid_values=None):
    """type_names: matched via 'Type Name equals' rules (the normal case).
    guid_values: matched via 'Type IfcGUID equals' rules instead — used only
    for a type name shared by BOTH the Architecture and Structure links,
    where a Type Name rule alone can't tell the two links' elements apart
    (see the module docstring's "SHARED TYPE NAME ACROSS DISCIPLINES" note
    and run()'s ambiguous_names handling)."""
    if not type_names and not guid_values:
        warnings.append(u"No concrete type names detected for '{}' — filter left "
                         u"unchanged.".format(filter_name))
        return _find_existing_filter(filter_name)

    cat_ids = SCG.List[DB.ElementId]([DB.ElementId(b) for b in category_bics])

    rules = []

    if type_names:
        bip_name = getattr(DB.BuiltInParameter, "ALL_MODEL_TYPE_NAME", None)
        if bip_name is None:
            warnings.append(u"ALL_MODEL_TYPE_NAME is not available in this Revit version — "
                             u"could not build filter '{}'.".format(filter_name))
        else:
            name_provider = DB.ParameterValueProvider(DB.ElementId(bip_name))
            for name in sorted(type_names):
                try:
                    rule = DB.FilterStringRule(name_provider, DB.FilterStringEquals(), name)
                    rules.append(DB.ElementParameterFilter(rule))
                except Exception as ex:
                    warnings.append(u"Could not build a filter rule for type '{}': {}".format(name, ex))

    if guid_values:
        bip_guid = getattr(DB.BuiltInParameter, "IFC_TYPE_GUID", None)
        if bip_guid is None:
            warnings.append(u"IFC_TYPE_GUID is not available in this Revit version — could not "
                             u"disambiguate shared type name(s) for '{}'.".format(filter_name))
        else:
            guid_provider = DB.ParameterValueProvider(DB.ElementId(bip_guid))
            for guid in sorted(guid_values):
                try:
                    rule = DB.FilterStringRule(guid_provider, DB.FilterStringEquals(), guid)
                    rules.append(DB.ElementParameterFilter(rule))
                except Exception as ex:
                    warnings.append(u"Could not build a Type IfcGUID filter rule for '{}': {}".format(
                        filter_name, ex))

    if not rules:
        warnings.append(u"No usable filter rules for '{}'.".format(filter_name))
        return None

    combined = rules[0] if len(rules) == 1 else DB.LogicalOrFilter(SCG.List[DB.ElementFilter](rules))

    pfe = _find_existing_filter(filter_name)
    try:
        if pfe is None:
            pfe = DB.ParameterFilterElement.Create(doc, filter_name, cat_ids)
        else:
            pfe.SetCategories(cat_ids)
        pfe.SetElementFilter(combined)
    except Exception as ex:
        warnings.append(u"Could not create/update view filter '{}': {}".format(filter_name, ex))
        return None

    return pfe


def apply_filter_to_target(target, pfe, ogs, warnings, label):
    if pfe is None:
        return
    try:
        applied_ids = [i.IntegerValue for i in target.GetFilters()]
        if pfe.Id.IntegerValue not in applied_ids:
            target.AddFilter(pfe.Id)
        target.SetFilterOverrides(pfe.Id, ogs)
    except Exception as ex:
        warnings.append(u"Could not apply the {} view filter: {}".format(label, ex))


def apply_or_clear_hide_filter(target, pfe, warnings, label):
    """Manual "hide these Type Names" filter (see FILTER_NAME_MANUAL_HIDE) —
    View.SetFilterVisibility(False), not SetFilterOverrides, since the goal
    is to genuinely suppress the matched elements, not recolor them.

    pfe is None when ManualHideTypeNames is currently empty — in that case
    this actively REMOVES any stale filter of this name the template still
    has attached (RemoveFilter, confirmed real via reflection), rather than
    just skipping the add: the shared template is reused across runs, so a
    name removed from Settings.json must stop hiding things it used to,
    the same "reset every run, don't just skip" lesson already learned for
    the non-concrete fallback override."""
    if pfe is None:
        stale = _find_existing_filter(FILTER_NAME_MANUAL_HIDE)
        if stale is None:
            return
        try:
            applied_ids = [i.IntegerValue for i in target.GetFilters()]
            if stale.Id.IntegerValue in applied_ids:
                target.RemoveFilter(stale.Id)
        except Exception as ex:
            warnings.append(u"Could not remove the stale {} filter: {}".format(label, ex))
        return
    try:
        applied_ids = [i.IntegerValue for i in target.GetFilters()]
        if pfe.Id.IntegerValue not in applied_ids:
            target.AddFilter(pfe.Id)
        target.SetFilterVisibility(pfe.Id, False)
    except Exception as ex:
        warnings.append(u"Could not apply the {} hide filter: {}".format(label, ex))


# ─────────────────────────────────────────────────────────────────────────────
# GRAPHIC OVERRIDES  (Step 8D)
# ─────────────────────────────────────────────────────────────────────────────

def get_solid_line_pattern_id():
    try:
        return DB.LinePatternElement.GetSolidPatternId()
    except Exception:
        return DB.ElementId.InvalidElementId


def get_solid_fill_pattern_id():
    """First Drafting-target FillPatternElement whose FillPattern.IsSolidFill
    is True (a real, distinct property from pattern *name* — confirmed via
    reflecting the installed RevitAPI.dll — so this works regardless of
    locale/naming and doesn't depend on a pattern literally being named
    "Solid fill"). Used by the gray fallback override so it can explicitly
    win over a linked type's own hardcoded Type Properties fill (see
    _apply_coordination_categories' gray_ogs) — a plain color/DetailLevel
    override with no Cut/Surface pattern set at all has nothing for
    "Fine" to apply to, and lets the element's own native fill through."""
    try:
        for fpe in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
            try:
                fp = fpe.GetFillPattern()
                if fp is not None and fp.Target == DB.FillPatternTarget.Drafting and fp.IsSolidFill:
                    return fpe.Id
            except Exception:
                continue
    except Exception:
        pass
    return DB.ElementId.InvalidElementId


def _fuzzy_tokens_from_pattern_name(name):
    base = (name or u"").split(u"-")[0]
    words = [w.upper() for w in base.replace(u",", u" ").split() if w.isalpha()]
    return words or [u"DIAGONAL"]


def find_fill_pattern_id(exact_name, warnings, label):
    """Exact drafting-pattern name match, else a drafting pattern containing
    every word from the configured name, else any drafting pattern with
    'DIAGONAL' in its name, else InvalidElementId (+ a warning). Logs which
    tier matched and the resolved pattern's actual name/id via logger.info
    (not `warnings` — this is routine diagnostic info) on every call, not
    just on total failure: live-testing found Structure (red) still
    rendering solid while Architecture (blue) correctly hatched, from the
    exact same code path applied to both colors — the two ONLY differ in
    the pattern *name* they search for (StructPatternName/ArchPatternName)
    and how many type names their filter matches (48 vs 5 in that test).
    If Structure's search silently resolves to a DIFFERENT actual pattern
    than Architecture's (e.g. a much finer/denser one that reads as solid
    at typical zoom, since these are Drafting-target patterns — fixed
    paper-space spacing regardless of model zoom), that would explain the
    visual mismatch without any override/filter logic being wrong at all.
    This makes that immediately checkable from the pyRevit output window
    on the next run, instead of guessing from a screenshot."""
    fuzzy_tokens = _fuzzy_tokens_from_pattern_name(exact_name)

    drafting = []
    for fpe in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        try:
            fp = fpe.GetFillPattern()
            if fp is not None and fp.Target == DB.FillPatternTarget.Drafting:
                drafting.append(fpe)
        except Exception:
            continue

    def _log_and_return(fpe, tier):
        try:
            logger.info(u"{}: cut hatch pattern for '{}' resolved via {} tier -> '{}' (id {})".format(
                label, exact_name, tier, _elem_name(fpe), fpe.Id.IntegerValue))
        except Exception:
            pass
        return fpe.Id

    for fpe in drafting:
        if _elem_name(fpe) == exact_name:
            return _log_and_return(fpe, u"exact-name")

    for fpe in drafting:
        nm = _elem_name(fpe).upper()
        if all(tok in nm for tok in fuzzy_tokens):
            return _log_and_return(fpe, u"fuzzy-token ({})".format(u"+".join(fuzzy_tokens)))

    for fpe in drafting:
        if u"DIAGONAL" in _elem_name(fpe).upper():
            return _log_and_return(fpe, u"any-diagonal fallback")

    try:
        logger.info(u"{}: cut hatch pattern for '{}' — NO MATCH at any tier ({} drafting "
                    u"patterns scanned).".format(label, exact_name, len(drafting)))
    except Exception:
        pass
    warnings.append(
        u"No fill pattern named '{}' (and no diagonal fallback) was found for the "
        u"{} cut hatch pattern — that override was skipped.".format(exact_name, label))
    return DB.ElementId.InvalidElementId


def _settings_color(settings, key, fallback):
    c = settings.get(key) or {}
    try:
        return DB.Color(int(c.get(u"R", 0)), int(c.get(u"G", 0)), int(c.get(u"B", 0)))
    except Exception:
        return fallback


def build_colored_override(color, pattern_name, warnings, label):
    """Cut AND Surface/Projection, both hatched — Surface was dropped
    entirely a few rounds back (elements were rendering as 100% solid,
    opaque colored blocks), then live-testing found a DIFFERENT symptom
    that needs Surface back: walls (unlike columns, which reliably span
    the full cut-plane height) can have their top BELOW the view's cut
    plane, in which case Revit shows them via Projection/Surface, not
    Cut, at all — meaning a Cut-only override never touches them, and
    they fall through to their own native Surface pattern, the exact same
    class of bug as the Coarse Scale Fill Pattern issue, just on the
    Surface side instead of Cut. Re-added, but NOT the same way as
    before — the two actual causes of the original "solid block" bug are
    both still avoided:
      (1) SetSurfaceTransparency is NOT used here at all (it only ever
          affected Shaded/Realistic visual styles, never Hidden Line —
          the default, and by far the most common, style for a 2D
          coordination plan view — so it was never a reliable mechanism
          for anything and only added confusion).
      (2) SetSurfaceForegroundPatternColor is only ever set when fill_id
          actually resolved to a real pattern (same guard already used
          for the Cut side) — setting a color with no pattern in place
          tints the element's own inherent Surface pattern instead
          (often Solid), which was the second real contributor to the
          original bug.
    SetDetailLevel(Fine), below, forces Fine for the WHOLE element this
    override applies to — Cut and Surface both — so it protects both
    sides against Coarse-driven native patterns equally; re-adding
    Surface here doesn't reopen that particular risk.

    All boundary lines are Solid — a dashed line pattern for columns was
    tried and then explicitly reversed per earlier live-testing feedback.
    Only the line color and the diagonal hatch differ between Structure
    (red) and Architecture (blue).

    Grids have no Cut OR Surface representation in the sense this
    function overrides (they're a projection-LINE-only datum category,
    no fillable face at all) — see build_grid_line_override for the
    separate, minimal override that actually colors a Grid line.

    THE REAL FIX for Coarse Scale Fill Pattern (a linked element's own
    Type Property, e.g. a wall type hardcoded to solid blue fill, winning
    over this hatch when the effective detail level is Coarse — see the
    module docstring's DEAD END paragraph for everything that does NOT
    work): OverrideGraphicSettings.SetDetailLevel(ViewDetailLevel), found
    by reflecting the installed RevitAPI.dll one property class over from
    where several earlier rounds were looking. This is a COMPLETELY
    different class from RevitLinkGraphicsSettings — it's the override
    object already being applied to elements via View.SetFilterOverrides
    (see apply_filter_to_target), available since Revit 2014, and (unlike
    everything tried against RevitLinkGraphicsSettings/the linked document
    itself) forces the elements THIS FILTER matches to render as if the
    detail level were Fine, regardless of the link's or the linked view's
    actual detail level — no linked-document Transaction involved at all,
    confirmed by actually instantiating the class and calling
    SetDetailLevel(Fine) live via .NET reflection, not just reading a
    member list."""
    ogs = DB.OverrideGraphicSettings()

    try:
        ogs.SetDetailLevel(DB.ViewDetailLevel.Fine)
    except Exception as ex:
        warnings.append(u"{}: could not force the override's own detail level to Fine: {}".format(label, ex))

    line_id = get_solid_line_pattern_id()

    # ── Cut ──────────────────────────────────────────────────────────────
    try:
        ogs.SetCutLineWeight(1)
    except Exception as ex:
        warnings.append(u"{}: could not set cut line weight: {}".format(label, ex))
    if line_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutLinePatternId(line_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the cut line pattern: {}".format(label, ex))
    try:
        ogs.SetCutLineColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the cut line color: {}".format(label, ex))
    try:
        ogs.SetCutBackgroundPatternVisible(False)
    except Exception as ex:
        warnings.append(u"{}: could not disable the cut background pattern: {}".format(label, ex))
    try:
        ogs.SetCutBackgroundPatternId(DB.ElementId.InvalidElementId)
    except Exception:
        pass
    try:
        ogs.SetCutForegroundPatternVisible(True)
    except Exception as ex:
        warnings.append(u"{}: could not enable the cut foreground pattern: {}".format(label, ex))

    # ── Surface / Projection ────────────────────────────────────────────
    try:
        ogs.SetProjectionLineWeight(1)
    except Exception as ex:
        warnings.append(u"{}: could not set projection line weight: {}".format(label, ex))
    if line_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetProjectionLinePatternId(line_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the projection line pattern: {}".format(label, ex))
    try:
        ogs.SetProjectionLineColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the projection line color: {}".format(label, ex))
    try:
        ogs.SetSurfaceBackgroundPatternVisible(False)
    except Exception as ex:
        warnings.append(u"{}: could not disable the surface background pattern: {}".format(label, ex))
    try:
        ogs.SetSurfaceBackgroundPatternId(DB.ElementId.InvalidElementId)
    except Exception:
        pass
    try:
        ogs.SetSurfaceForegroundPatternVisible(True)
    except Exception as ex:
        warnings.append(u"{}: could not enable the surface foreground pattern: {}".format(label, ex))

    # ── Shared hatch pattern (resolved once, applied to both) ──────────
    fill_id = find_fill_pattern_id(pattern_name, warnings, label)
    if fill_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutForegroundPatternId(fill_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the cut foreground pattern id: {}".format(label, ex))
        try:
            ogs.SetCutForegroundPatternColor(color)
        except Exception as ex:
            warnings.append(u"{}: could not set the cut foreground pattern color: {}".format(label, ex))
        try:
            ogs.SetSurfaceForegroundPatternId(fill_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the surface foreground pattern id: {}".format(label, ex))
        try:
            ogs.SetSurfaceForegroundPatternColor(color)
        except Exception as ex:
            warnings.append(u"{}: could not set the surface foreground pattern color: {}".format(label, ex))
    else:
        warnings.append(
            u"{}: no diagonal hatch pattern could be resolved, so the cut/surface pattern "
            u"colors were skipped too (setting a color with no pattern override in place "
            u"tints the element's own default pattern instead — often Solid fill, exactly "
            u"the opaque-block bug) — only the cut/projection LINE colors were applied.".format(label))

    return ogs


def build_grid_line_override(color, warnings, label):
    """Grids are a projection-only datum category — no Cut representation
    to override, unlike Walls/Columns/Framing/Foundations. build_colored_
    override() above is Cut-only now, so grid coloring needs this separate,
    minimal override: just the projection line color + solid line pattern,
    the only things that actually paint a colored, continuous Grid line."""
    ogs = DB.OverrideGraphicSettings()
    try:
        ogs.SetProjectionLineColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the Grid line color: {}".format(label, ex))

    line_id = get_solid_line_pattern_id()
    if line_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetProjectionLinePatternId(line_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the Grid line pattern: {}".format(label, ex))

    return ogs


def build_section_arch_override(color, warnings):
    """Architecture's Section-only treatment (FILTER_NAME_ARCH_SECTION) —
    solid (not hatch) Cut+Surface fill in `color`, Halftone, and
    SECTION_ARCH_TRANSPARENCY% Surface Transparency, applied uniformly to
    every type this filter matches (no concrete/non-concrete split, unlike
    Structure or Plans — see the SECTIONS constants block). SetHalftone
    works regardless of Visual Style; SetSurfaceTransparency only has a
    VISIBLE effect in Shaded/Realistic (confirmed earlier this project,
    see build_colored_override's docstring) — set anyway since it's
    harmless in Hidden Line and correct if the view ever uses Shaded."""
    ogs = DB.OverrideGraphicSettings()
    try:
        ogs.SetHalftone(True)
    except Exception as ex:
        warnings.append(u"Architecture (section): could not set halftone: {}".format(ex))
    try:
        ogs.SetSurfaceTransparency(SECTION_ARCH_TRANSPARENCY)
    except Exception as ex:
        warnings.append(u"Architecture (section): could not set transparency: {}".format(ex))

    line_id = get_solid_line_pattern_id()
    if line_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutLinePatternId(line_id)
            ogs.SetProjectionLinePatternId(line_id)
        except Exception as ex:
            warnings.append(u"Architecture (section): could not set the line pattern: {}".format(ex))
    try:
        ogs.SetCutLineColor(color)
        ogs.SetProjectionLineColor(color)
    except Exception as ex:
        warnings.append(u"Architecture (section): could not set the line color: {}".format(ex))

    solid_fill_id = get_solid_fill_pattern_id()
    if solid_fill_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutForegroundPatternVisible(True)
            ogs.SetCutForegroundPatternId(solid_fill_id)
            ogs.SetCutForegroundPatternColor(color)
            ogs.SetSurfaceForegroundPatternVisible(True)
            ogs.SetSurfaceForegroundPatternId(solid_fill_id)
            ogs.SetSurfaceForegroundPatternColor(color)
        except Exception as ex:
            warnings.append(u"Architecture (section): could not set the solid fill: {}".format(ex))
    else:
        warnings.append(
            u"Architecture (section): no solid fill pattern could be resolved — those "
            u"elements may show their own native Type Properties fill instead of solid blue.")
    return ogs


# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATIC SHEET CREATION  (Refinement 2)
# ─────────────────────────────────────────────────────────────────────────────

def find_title_block_symbol(warnings):
    """Exact-token match on Family Name or Type Name first; else a manual
    forms.SelectFromList pick among every loaded title block type."""
    try:
        symbols = list(DB.FilteredElementCollector(doc)
                          .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                          .WhereElementIsElementType()
                          .ToElements())
    except Exception as ex:
        warnings.append(u"Could not collect title block types: {}".format(ex))
        return None

    if not symbols:
        warnings.append(u"No title block family types are loaded in this project — "
                         u"sheet creation was skipped.")
        return None

    def _fam_name(sym):
        try:
            return _elem_name(sym.Family)
        except Exception:
            return u""

    token = TITLEBLOCK_TOKEN.upper()
    for sym in symbols:
        try:
            if token in _fam_name(sym).upper() or token in _elem_name(sym).upper():
                return sym
        except Exception:
            continue

    label_map = {}
    for sym in symbols:
        try:
            label = u"{} : {}".format(_fam_name(sym), _elem_name(sym))
            label_map[label] = sym
        except Exception:
            continue
    labels = sorted(label_map.keys())
    if not labels:
        warnings.append(u"Could not read any title block type's name — sheet creation was skipped.")
        return None

    picked = ebui.pick_from_list(
        labels, title=u"Select Title Block",
        prompt=u"'{}' was not found — pick a title block for the coordination "
               u"sheet.".format(TITLEBLOCK_TOKEN))
    if not picked:
        warnings.append(u"No title block selected — sheet creation was skipped.")
        return None
    return label_map.get(picked)


def _view_is_on_a_sheet(view):
    try:
        for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
            if vp.ViewId == view.Id:
                return True
    except Exception:
        pass
    return False


def _next_sheet_number(warnings):
    best = 0
    try:
        for sh in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            num = sh.SheetNumber or u""
            if num.upper().startswith(SHEET_NUMBER_PREFIX.upper()):
                m = re.match(r"^\D*(\d+)", num[len(SHEET_NUMBER_PREFIX):])
                if m:
                    try:
                        val = int(m.group(1))
                        if val > best:
                            best = val
                    except Exception:
                        continue
    except Exception as ex:
        warnings.append(u"Could not scan existing sheet numbers: {}".format(ex))
    return u"{}{:03d}".format(SHEET_NUMBER_PREFIX, best + 1)


def _get_view_for_sheet(view, template, warnings):
    """Step: safety check. A view can only be placed on one sheet — if the
    active view is already on one, duplicate it (WithDetailing carries over
    the element-level hides/overrides already applied) and use the copy.

    `template` may be the fallback active view itself, not a real
    ViewTemplate (see ensure_view_template) — only re-assign ViewTemplateId
    when it's genuinely a template; in fallback mode Duplicate(WithDetailing)
    already copied the view-level category hides/overrides directly, so
    there's nothing template-related to (re-)apply."""
    if not _view_is_on_a_sheet(view):
        return view
    try:
        new_view_id = view.Duplicate(DB.ViewDuplicateOption.WithDetailing)
        new_view = doc.GetElement(new_view_id)
    except Exception as ex:
        warnings.append(u"The active view is already on a sheet and could not be "
                         u"duplicated for a new one: {}".format(ex))
        return None
    if getattr(template, "IsTemplate", False):
        try:
            new_view.ViewTemplateId = template.Id
        except Exception as ex:
            warnings.append(u"Could not apply the '{}' template to the duplicated view: {}".format(
                TEMPLATE_NAME, ex))
    return new_view


def _create_and_place_sheet(view_for_sheet, view_name, titleblock_symbol, warnings):
    try:
        if not titleblock_symbol.IsActive:
            titleblock_symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass

    try:
        sheet = DB.ViewSheet.Create(doc, titleblock_symbol.Id)
    except Exception as ex:
        warnings.append(u"Could not create the coordination sheet: {}".format(ex))
        return None

    number = _next_sheet_number(warnings)
    try:
        sheet.SheetNumber = number
    except Exception as ex:
        warnings.append(u"Could not set the sheet number to '{}': {}".format(number, ex))
    try:
        sheet.Name = view_name
    except Exception as ex:
        warnings.append(u"Could not set the sheet name to '{}': {}".format(view_name, ex))

    try:
        viewport = DB.Viewport.Create(doc, sheet.Id, view_for_sheet.Id, DB.XYZ.Zero)
    except Exception as ex:
        warnings.append(u"Could not place the view on the sheet: {}".format(ex))
        return sheet

    try:
        tb_inst = (DB.FilteredElementCollector(doc, sheet.Id)
                     .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                     .WhereElementIsNotElementType()
                     .FirstElement())
        if tb_inst is not None:
            bbox = tb_inst.get_BoundingBox(sheet)
            if bbox is not None:
                center = DB.XYZ((bbox.Min.X + bbox.Max.X) / 2.0,
                                 (bbox.Min.Y + bbox.Max.Y) / 2.0, 0)
                viewport.SetBoxCenter(center)
    except Exception as ex:
        warnings.append(u"Could not center the viewport on the title block: {}".format(ex))

    return sheet


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run():
    active_view = get_view_and_validate()
    if active_view is None:
        return

    settings = cfgmod.load_settings()

    links = get_all_link_instances()
    if not links:
        TaskDialog.Show(u"EasyBIM — Coordination Graphics",
                         u"No linked Revit models were found in this project.")
        return

    # DIAGNOSTIC (added 2026-08-30, chasing a live "link reports hidden but
    # still visible/pickable after the tool runs" report): a linked FILE can
    # be placed as more than one separate RevitLinkInstance (e.g. once per
    # placement/location) — get_all_link_instances() already handles each
    # instance independently, but if only SOME instances of a duplicated
    # file end up hidden, the file still looks "on" overall. Surfaced in the
    # result dialog unconditionally so this is checkable without opening
    # Manage Links and counting positions by hand.
    _link_name_counts = {}
    for _li in links:
        _link_name_counts[_li[u"name"]] = _link_name_counts.get(_li[u"name"], 0) + 1
    duplicate_link_names = sorted(n for n, c in _link_name_counts.items() if c > 1)

    memory = cfgmod.load_link_memory(doc)
    # Back-compat: older memory files (before multi-select) stored a single
    # 'arch_uid'/'struct_uid' — treat that as a one-item list if the new
    # plural keys aren't there yet.
    arch_memory_uids = memory.get(u"arch_uids") or ([memory[u"arch_uid"]] if memory.get(u"arch_uid") else [])
    struct_memory_uids = memory.get(u"struct_uids") or ([memory[u"struct_uid"]] if memory.get(u"struct_uid") else [])

    arch_default    = _role_default_multi(links, arch_memory_uids, settings.get(u"ArchLinkKeywords"))
    struct_default  = _role_default_multi(links, struct_memory_uids, settings.get(u"StructLinkKeywords"))
    traffic_default = _role_default(links, memory.get(u"traffic_uid"), settings.get(u"TrafficLinkKeywords"))
    use_traffic_default = bool(memory.get(u"use_traffic")) and traffic_default[0] is not None

    try:
        host_level_obj = active_view.GenLevel
    except Exception:
        host_level_obj = None
    level_name = u"—"
    if host_level_obj is not None:
        level_name = _elem_name(host_level_obj) or u"—"
    view_info = {
        u"name"     : _elem_name(active_view),
        u"view_type": active_view.ViewType.ToString(),
        u"level"    : level_name,
        u"level_obj": host_level_obj,
        u"basement" : is_basement_view(active_view),
    }

    scope_boxes = get_all_scope_boxes()
    batch_views = get_eligible_views_for_batch(active_view)

    dlg = LinkPickerDialog(links, {
        u"arch": arch_default, u"struct": struct_default, u"traffic": traffic_default,
        u"use_traffic": use_traffic_default,
    }, view_info, scope_boxes, batch_views, active_view)
    dlg.show()
    if dlg.cancelled:
        return

    selected_views = dlg.selected_views or [active_view]
    arch_links, struct_links = dlg.arch_links, dlg.struct_links
    traffic_link, use_traffic = dlg.traffic_link, dlg.use_traffic
    scope_box = dlg.scope_box
    arch_linked_view    = dlg.arch_linked_view
    struct_linked_view  = dlg.struct_linked_view
    traffic_linked_view = dlg.traffic_linked_view

    # Settings may have been edited mid-wizard via the gear icon — reload so
    # the transactions below (colors, patterns, concrete/exclude keywords)
    # use whatever is currently saved, not the snapshot from before dlg.show().
    settings = cfgmod.load_settings()

    cfgmod.save_link_memory(doc, {
        u"arch_uids"  : [li[u"instance"].UniqueId for li in arch_links],
        u"struct_uids": [li[u"instance"].UniqueId for li in struct_links],
        u"traffic_uid": traffic_link[u"instance"].UniqueId if traffic_link else memory.get(u"traffic_uid"),
        u"use_traffic": use_traffic,
    })

    global_warnings = []
    chosen_ids = set(li[u"id"].IntegerValue for li in arch_links)
    chosen_ids |= set(li[u"id"].IntegerValue for li in struct_links)
    if use_traffic and traffic_link:
        chosen_ids.add(traffic_link[u"id"].IntegerValue)

    # Resolved before any transaction (like the link picker) since it may
    # show a forms.SelectFromList prompt. None just means sheet creation is
    # skipped later — it never blocks the graphics/template/filter work.
    titleblock_symbol = find_title_block_symbol(global_warnings)

    host_clutter_bics = _resolve_categories(HOST_CLUTTER_CATEGORY_NAMES, global_warnings)
    link_model_bics   = _resolve_categories(LINK_MODEL_HIDE_CATEGORY_NAMES, global_warnings)
    section_link_model_bics = _resolve_categories(SECTION_LINK_MODEL_HIDE_CATEGORY_NAMES, global_warnings)
    wallnoncore_bics  = _resolve_categories([WALL_NONCORE_CATEGORY_NAME], global_warnings)
    annotation_bics   = _resolve_categories(LINK_ANNOTATION_HIDE_CATEGORY_NAMES, global_warnings)
    override_bics     = _resolve_categories(OVERRIDE_CATEGORY_NAMES, global_warnings)
    traffic_keep_bics = set(_resolve_categories(TRAFFIC_KEEP_CATEGORY_NAMES, global_warnings))

    # ── Shared setup (batch mode, added 2026-08-31): the deep-scan (Step 8)
    # reads the SELECTED LINKS' own documents directly — completely
    # independent of which host view is being processed — and the two
    # colored View Filters (FILTER_NAME_STRUCT/FILTER_NAME_ARCH) are shared
    # Document elements applied to the ONE shared TEMPLATE_NAME template
    # every selected view ends up using. So all of this is
    # computed ONCE here, not once per view — running N redundant deep-
    # scans of the exact same links for N selected views would be pure
    # waste. Runs in its own transaction, committed before the per-view
    # loop, so a later per-view failure can never roll back this shared
    # setup work.
    t0 = DB.Transaction(doc, u"EasyBIM: Coordination Graphics — shared setup")
    t0.Start()
    try:
        # URGENT, runs first, before anything else: delete any
        # crash-causing whitelist filters a prior run already created (see
        # _cleanup_stale_whitelist_filters) — confirmed to crash Revit when
        # the user opens the Filters tab of a View/Template Properties
        # dialog.
        _cleanup_stale_whitelist_filters(global_warnings)
        _cleanup_stale_column_filters(global_warnings)

        # Smart Linked View: Architecture/Structure/Traffic Coarse-detail
        # warnings only (see _apply_smart_linked_view's docstring) — these
        # describe the LINKED view's own detail level, not anything about
        # the host view(s), so they only need to run once regardless of
        # how many views are selected.
        _apply_smart_linked_view(arch_linked_view, global_warnings, u"Architecture")
        _apply_smart_linked_view(struct_linked_view, global_warnings, u"Structure")
        if use_traffic and traffic_link:
            _ensure_linked_view_detail_level(traffic_linked_view, global_warnings, u"Traffic")

        # ── Step 8: deep-scan (every selected link, per role) + View Filters ───
        # struct_all_names (every Type Name in override_bics categories,
        # concrete or not) is only actually needed if a Section view is in
        # this batch, to build Structure's non-concrete fallback filter
        # below (see SECTION_TEMPLATE_NAME's docstring) -- collected
        # unconditionally anyway since it's the same scan already running,
        # at negligible extra cost. Architecture has no equivalent need any
        # more (see FILTER_NAME_ARCH_SECTION below instead) -- it gets its
        # own SEPARATE, WIDER scan further down, since Sections treat ALL
        # of Architecture's relevant content identically regardless of
        # concrete classification, unlike Structure.
        arch_names = set()
        arch_guids = {}
        for li in arch_links:
            arch_names |= _collect_concrete_type_names(
                li[u"doc"], override_bics, settings, global_warnings,
                u"Architecture ({})".format(li[u"name"]), guids_by_name=arch_guids)
        struct_names = set()
        struct_all_names = set()
        struct_guids = {}
        for li in struct_links:
            struct_names |= _collect_concrete_type_names(
                li[u"doc"], override_bics, settings, global_warnings,
                u"Structure ({})".format(li[u"name"]), guids_by_name=struct_guids,
                all_names_out=struct_all_names)

        # A type name found in BOTH links' scans can't be told apart by a
        # plain "Type Name equals" rule (a Filter Rule never sees which
        # linked document an element came from) — disambiguate via each
        # type's own Type IfcGUID instead, which reliably differs between
        # the two links even for identically-named types (see module
        # docstring "SHARED TYPE NAME ACROSS DISCIPLINES").
        ambiguous_names = arch_names & struct_names
        arch_safe_names, struct_safe_names = arch_names, struct_names
        arch_guid_values = struct_guid_values = None
        if ambiguous_names:
            arch_safe_names = arch_names - ambiguous_names
            struct_safe_names = struct_names - ambiguous_names

            def _guids_for(names, guids_by_name, side):
                out = set()
                missing = []
                for n in names:
                    g = set(x for x in (guids_by_name.get(n) or set()) if x)
                    if g:
                        out |= g
                    else:
                        missing.append(n)
                if missing:
                    global_warnings.append(
                        u"{}: could not read Type IfcGUID for shared type name(s) {} — "
                        u"excluded from coloring on this side to avoid mis-coloring. "
                        u"Rename the type distinctly in the source file to resolve "
                        u"permanently.".format(side, u", ".join(sorted(missing))))
                return out

            arch_guid_values = _guids_for(ambiguous_names, arch_guids, u"Architecture")
            struct_guid_values = _guids_for(ambiguous_names, struct_guids, u"Structure")
            global_warnings.append(
                u"Type name(s) shared by both Architecture and Structure links: {} — "
                u"disambiguated via each type's own Type IfcGUID instead of Type Name. "
                u"Consider renaming one side's type distinctly to remove the ambiguity "
                u"permanently.".format(u", ".join(sorted(ambiguous_names))))

        struct_pfe = build_or_update_type_name_filter(
            FILTER_NAME_STRUCT, override_bics, struct_safe_names, global_warnings, struct_guid_values)
        arch_pfe   = build_or_update_type_name_filter(
            FILTER_NAME_ARCH, override_bics, arch_safe_names, global_warnings, arch_guid_values)

        struct_color = _settings_color(settings, u"StructColor", DB.Color(200, 30, 30))
        arch_color   = _settings_color(settings, u"ArchColor", DB.Color(0, 70, 200))

        struct_ogs = build_colored_override(struct_color, settings.get(u"StructPatternName"),
                                             global_warnings, u"Structure")
        arch_ogs   = build_colored_override(arch_color, settings.get(u"ArchPatternName"),
                                             global_warnings, u"Architecture")
        struct_grid_ogs = build_grid_line_override(struct_color, global_warnings, u"Structure")
        arch_grid_ogs   = build_grid_line_override(arch_color, global_warnings, u"Architecture")

        # Section-only Structure non-concrete fallback FILTER (see SECTION_
        # TEMPLATE_NAME's docstring) — takes over the plan template's
        # blanket category-level white/gray fallback job for Structure's
        # own non-concrete elements, per-link via Type Name like the
        # concrete filter, since Sections skip that blanket fallback
        # entirely (a category override can't tell Architecture's elements
        # apart from Structure's). Built unconditionally (cheap set
        # arithmetic) but only ever APPLIED to a view in the per-view loop
        # below when that view is actually a Section.
        struct_nonconcrete_names = struct_all_names - struct_names
        section_fallback_ogs = _build_nonconcrete_fallback_ogs(global_warnings)
        struct_fallback_pfe = None
        if struct_nonconcrete_names:
            struct_fallback_pfe = build_or_update_type_name_filter(
                FILTER_NAME_STRUCT_FALLBACK, override_bics, struct_nonconcrete_names, global_warnings)

        # Architecture's Section-only filter — a SEPARATE, WIDER scan
        # (SECTION_ARCH_CATEGORY_NAMES: OVERRIDE_CATEGORY_NAMES + Floors +
        # Stairs) covering EVERY type Architecture has there, concrete or
        # not — Sections give all of it the identical solid-blue/halftone/
        # transparent treatment (build_section_arch_override), unlike
        # Structure or Plans. No classification needed, hence
        # _collect_all_type_names (a plain scan) instead of
        # _collect_concrete_type_names.
        section_arch_bics = _resolve_categories(SECTION_ARCH_CATEGORY_NAMES, global_warnings)
        arch_section_names = set()
        for li in arch_links:
            arch_section_names |= _collect_all_type_names(
                li[u"doc"], section_arch_bics, global_warnings,
                u"Architecture section ({})".format(li[u"name"]))
        arch_section_pfe = None
        if arch_section_names:
            arch_section_pfe = build_or_update_type_name_filter(
                FILTER_NAME_ARCH_SECTION, section_arch_bics, arch_section_names, global_warnings)
        arch_section_ogs = build_section_arch_override(arch_color, global_warnings)

        # Building elevation range (lowest/highest Level across every
        # selected Architecture/Structure link) — computed once here since
        # it only depends on the selected links, not on which view is
        # being processed; applied per-view (Sections only) in the loop
        # below via _apply_section_height_crop, per explicit request that
        # a Section's crop height should span the building's full height.
        section_min_elev, section_max_elev = _building_elevation_range(
            arch_links, struct_links, global_warnings)

        # Manual per-Type-Name HIDE filter (Settings.json's
        # ManualHideTypeNames, set via ARC/STR Settings' "Add from
        # Model..." picker) — an escape hatch for linked elements that
        # stay visible despite Step 5/6's automatic link- and DWG-hiding
        # (e.g. content inside a SELECTED Architecture/Structure link
        # itself, which those steps never touch at all). Covers BOTH Model
        # and Annotation categories, since a manually-picked rogue element
        # could be either.
        #
        # BUG FOUND AND FIXED (root-caused via live testing: the filter
        # never appeared in Revit's own Filters list at all, for ANY
        # picked element): an earlier version of this collected every
        # CategoryType.Model category from doc.Settings.Categories by
        # hand. Not every category Revit shows in that list is actually
        # usable in a ParameterFilterElement — ParameterFilterElement.
        # Create THROWS if even ONE category in the list it's given isn't
        # filterable, failing the ENTIRE filter creation, not just that
        # one category (confirmed via reflecting the installed
        # RevitAPI.dll: ParameterFilterUtilities.GetAllFilterableCategories
        # /RemoveUnfilterableCategories exist specifically because of this
        # constraint). Fixed by sourcing categories from
        # GetAllFilterableCategories() directly instead of hand-rolling
        # the list — Revit's own authoritative "these are safe to use in a
        # Filter" source, which also naturally includes the Annotation
        # categories the hand-rolled Model-only version excluded.
        #
        # SECOND BUG FOUND AND FIXED, same live-testing round: even with
        # GetAllFilterableCategories() (all individually filterable),
        # ParameterFilterElement.Create STILL threw — "One of the given
        # rules refers to a parameter that does not apply to this filter's
        # categories" — because "Type Name" (ALL_MODEL_TYPE_NAME) isn't a
        # valid/filterable parameter for every one of those ~150+
        # categories at once (e.g. purely 2D/analytical ones). The same
        # "one bad apple fails the whole Create call" behavior as the
        # category-list bug above, just for the RULE's parameter instead
        # of the category list. Fixed by checking, per candidate category,
        # whether ALL_MODEL_TYPE_NAME is actually in
        # ParameterFilterUtilities.GetFilterableParametersInCommon(doc,
        # [that one category]) — confirmed via reflection this is the
        # correct per-category applicability check (IsParameterApplicable
        # takes an Element, not a category, so it doesn't fit this
        # "before any element exists" use case) — and only keeping
        # categories that pass.
        manual_hide_names = set(settings.get(u"ManualHideTypeNames") or [])
        try:
            filterable_cat_ids = list(DB.ParameterFilterUtilities.GetAllFilterableCategories())
        except Exception as ex:
            filterable_cat_ids = []
            global_warnings.append(u"Could not read Revit's filterable-category list: {}".format(ex))
        type_name_bip = getattr(DB.BuiltInParameter, "ALL_MODEL_TYPE_NAME", None)
        manual_hide_bics = []
        if type_name_bip is not None and filterable_cat_ids:
            type_name_param_id = DB.ElementId(type_name_bip)
            for _cid in filterable_cat_ids:
                try:
                    single = SCG.List[DB.ElementId]([_cid])
                    common_params = DB.ParameterFilterUtilities.GetFilterableParametersInCommon(doc, single)
                    if not any(p.IntegerValue == type_name_param_id.IntegerValue for p in common_params):
                        continue
                    manual_hide_bics.append(DB.BuiltInCategory(_cid.IntegerValue))
                except Exception:
                    continue
        manual_hide_pfe = None
        if manual_hide_names:
            if not manual_hide_bics:
                global_warnings.append(
                    u"Manual Hide: no category supports filtering by Type Name in this "
                    u"Revit version — could not build the hide filter.")
            else:
                manual_hide_pfe = build_or_update_type_name_filter(
                    FILTER_NAME_MANUAL_HIDE, manual_hide_bics, manual_hide_names, global_warnings)

        t0.Commit()
    except Exception:
        t0.RollBack()
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics — Error",
            u"Coordination Graphics failed during shared setup and no changes were made:\n\n{}".format(
                traceback.format_exc())
        )
        return

    # ── Per-view processing (batch mode) ────────────────────────────────────
    # Everything below is genuinely per-view: template assignment (each
    # view needs its own ViewTemplateId set, even though the template
    # ELEMENT itself is the same shared, reused object), scope box, host
    # Grids hide, DWG-import hide, Traffic settings, grid coloring
    # (View.SetElementOverrides is a per-view operation), sheet creation,
    # and Step 5's hide-other-links + FINAL CHECK (both view-scoped). Each
    # view gets its OWN Transaction, so one view's failure never rolls back
    # another's already-committed results — matches the explicit "worst
    # case, just re-run that one view" tolerance this batch mode was built
    # around, instead of one giant all-or-nothing transaction.
    view_results = []
    all_mismatches = []
    for view in selected_views:
        vr = {
            u"name": _elem_name(view), u"warnings": [], u"sheet": None,
            u"other_links_summary": u"", u"traffic_summary": None, u"error": None,
        }
        w = vr[u"warnings"]
        is_section = _is_section_view(view)
        # Traffic is never shown in Sections regardless of the wizard's
        # Traffic checkbox (see SECTION_TEMPLATE_NAME's docstring, point
        # 2) — excluding its id here means Step 5 below treats it exactly
        # like any other non-selected "other" link for this one view,
        # fully hidden instead of restricted-to-Parking. Plan views are
        # completely unaffected (view_chosen_ids == chosen_ids for them).
        view_chosen_ids = chosen_ids
        if is_section and use_traffic and traffic_link:
            view_chosen_ids = chosen_ids - set([traffic_link[u"id"].IntegerValue])
        t = DB.Transaction(doc, u"EasyBIM: Coordination Graphics — {}".format(vr[u"name"]))
        t.Start()
        try:
            # Unassign any template already active on this view FIRST. Two
            # reasons: (1) the original spec requirement — an active
            # template blocks manual per-view overrides; (2)
            # View.CreateViewTemplate() can fail when the view already has
            # a template controlling it, which is the likely cause if
            # template setup below ever needs its fallback path.
            try:
                if view.ViewTemplateId != DB.ElementId.InvalidElementId:
                    view.ViewTemplateId = DB.ElementId.InvalidElementId
            except Exception as ex:
                w.append(u"Could not clear the view's existing template: {}".format(ex))

            # Scope box crop, if one was picked — applied identically to
            # every selected view. If different views need different crops
            # (e.g. different buildings), pick no scope box here and crop
            # manually afterward, or run this tool again per view.
            apply_scope_box(view, scope_box, w)

            # ── Step 3: shared view template (host clutter + Step 6 categories) ────
            # ensure_view_template() never returns None — on any failure it
            # falls back to configuring the active view directly instead of
            # aborting. Finds the SAME template by name on every call after
            # the first, so re-running the category setup per view is safe
            # (idempotent) even though it's mildly redundant.
            #
            # Sections get a SEPARATE template (SECTION_TEMPLATE_NAME),
            # Floors left visible (section_link_model_bics), and
            # apply_fallback=False — see that constant's docstring for the
            # full reasoning. Structure's concrete filter/color is applied
            # exactly like plans, plus its own non-concrete fallback filter
            # (replacing the blanket category override Sections skip).
            # Architecture gets ONE filter covering everything relevant
            # (concrete or not, plus Floors/Stairs) with the solid-blue/
            # halftone/transparent treatment — no arch_pfe/arch_ogs at all
            # on this template.
            if is_section:
                template = ensure_view_template(
                    view, host_clutter_bics, section_link_model_bics,
                    wallnoncore_bics, annotation_bics, override_bics, w,
                    template_name=SECTION_TEMPLATE_NAME, old_names=(), apply_fallback=False)
                apply_filter_to_target(template, struct_pfe, struct_ogs, w, u"Structure")
                apply_filter_to_target(template, struct_fallback_pfe, section_fallback_ogs, w,
                                        u"Structure non-concrete fallback")
                apply_filter_to_target(template, arch_section_pfe, arch_section_ogs, w,
                                        u"Architecture (section)")
                # Crop height = the building's own elevation range (see
                # _building_elevation_range/_apply_section_height_crop),
                # per explicit request — only the vertical extent changes,
                # the section's existing horizontal cut width and depth
                # are left exactly as they were.
                _apply_section_height_crop(view, section_min_elev, section_max_elev, w)
            else:
                template = ensure_view_template(view, host_clutter_bics, link_model_bics,
                                                 wallnoncore_bics, annotation_bics,
                                                 override_bics, w)
                apply_filter_to_target(template, struct_pfe, struct_ogs, w, u"Structure")
                apply_filter_to_target(template, arch_pfe, arch_ogs, w, u"Architecture")
            apply_or_clear_hide_filter(template, manual_hide_pfe, w, u"Manual Hide")

            # ── Refinement 1: host Grids hidden (element-level — see docstring) ────
            _hide_host_grids(view, w)

            # ── Step 6 (DWGs): hide imported DWG categories inside Arch/Struct ─────
            basement = is_basement_view(view)
            for li in arch_links:
                _hide_dwg_imports_in_link(view, li, basement, w)
            for li in struct_links:
                _hide_dwg_imports_in_link(view, li, basement, w)

            # ── Step 7: Traffic link ─────────────────────────────────────────────
            # Skipped entirely for Sections (view_chosen_ids already
            # excludes the Traffic link's id above, so Step 5 below hides
            # it fully like any other non-selected link, instead of giving
            # it the Parking-only restriction).
            if use_traffic and traffic_link and not is_section:
                vr[u"traffic_summary"] = _restrict_traffic_link_visibility(
                    view, traffic_link, traffic_keep_bics, w)
                traffic_view_id = traffic_linked_view[u"view"].Id if traffic_linked_view else None
                # Targets `view` directly, NOT `template` — see
                # _ensure_rvt_links_not_template_controlled's docstring:
                # writing link display settings to the template while
                # "V/G Overrides RVT Links" was template-controlled is what
                # broke Step 5's per-view link hides. Duplicate
                # (WithDetailing), used for a sheet's copy
                # (_get_view_for_sheet), already carries this view-specific
                # setting over on its own — no template needed.
                _apply_link_display_settings(view, traffic_link[u"id"], traffic_view_id, w, u"Traffic")
                _set_traffic_halftone(view, traffic_link, w)

            # ── Refinement 1: color Grids inside every selected Arch/Struct link ───
            # View.SetElementOverrides is per-view, so this can't be hoisted
            # into the shared setup above even though the OGS objects
            # (struct_grid_ogs/arch_grid_ogs) themselves are.
            for li in struct_links:
                _color_link_grids(view, li, struct_grid_ogs, w, u"Structure")
            for li in arch_links:
                _color_link_grids(view, li, arch_grid_ogs, w, u"Architecture")

            # ── Refinement 2: automatic sheet creation ──────────────────────────
            if titleblock_symbol is not None:
                view_for_sheet = _get_view_for_sheet(view, template, w)
                if view_for_sheet is not None:
                    vr[u"sheet"] = _create_and_place_sheet(
                        view_for_sheet, _elem_name(view_for_sheet), titleblock_symbol, w)

            # ── Refinement 3: 2D mismatch detection (Structure vs Architecture) ─
            # Runs per view (not hoisted into the shared t0 setup like the
            # deep-scan above) specifically so each mismatch can be tagged with
            # THIS view's name/sheet for BCF export below — read-only (no
            # document edits), so it needs no transaction of its own and any
            # exception here is caught by this view's own except block below,
            # same as everything else in this try. Every Architecture/
            # Structure link COMBINATION selected for this batch is checked,
            # not just the first of each.
            sheet_number = vr[u"sheet"].SheetNumber if vr[u"sheet"] is not None else None
            for arch_li in arch_links:
                for struct_li in struct_links:
                    view_issues = detect_2d_mismatches(
                        doc, arch_li, struct_li, MISMATCH_2D_TOLERANCE_FT, override_bics, settings)
                    for issue in view_issues:
                        issue[u"view_name"] = vr[u"name"]
                        issue[u"sheet_number"] = sheet_number
                    all_mismatches.extend(view_issues)

            # ── Step 5: hide every other link (e.g. MEP) ────────────────────────
            # Runs LAST, after the template/filter work above — see the
            # module docstring's "WHY 'V/G OVERRIDES RVT LINKS' IS NOW KEPT
            # *NOT* TEMPLATE-CONTROLLED" for why order matters here.
            # other_link_ids is ALREADY a typed .NET List[ElementId]
            # (SCG.List), not a plain Python list. Every outcome (hidden /
            # already-hidden / un-hideable / exception) is named explicitly
            # and folded into other_links_summary below, which the final
            # TaskDialog prints unconditionally -- not just on failure --
            # so "still visible" is checkable directly from the result
            # dialog instead of needing to dig through warnings or guess.
            other_link_ids = SCG.List[DB.ElementId]()
            already_hidden_names = []
            unhideable_names = []
            for li in links:
                if li[u"id"].IntegerValue in view_chosen_ids:
                    continue
                try:
                    if li[u"instance"].IsHidden(view):
                        already_hidden_names.append(li[u"name"])
                        continue
                    if li[u"instance"].CanBeHidden(view):
                        other_link_ids.Add(li[u"id"])
                    else:
                        unhideable_names.append(li[u"name"])
                        w.append(
                            u"Link '{}' could not be hidden — Revit reports it as "
                            u"un-hideable in this view (CanBeHidden=False), so it "
                            u"remains visible.".format(li[u"name"]))
                except Exception as ex:
                    unhideable_names.append(li[u"name"])
                    w.append(u"Could not hide link '{}': {}".format(li[u"name"], ex))

            newly_hidden_count = 0
            if other_link_ids.Count:
                try:
                    view.HideElements(other_link_ids)
                    newly_hidden_count = other_link_ids.Count
                except Exception as ex:
                    unhideable_names.extend(li[u"name"] for li in links
                                             if li[u"id"].IntegerValue in
                                             set(i.IntegerValue for i in other_link_ids))
                    w.append(u"Could not hide {} other link(s): {}".format(
                        other_link_ids.Count, ex))

                still_visible_after = []
                for li in links:
                    if li[u"id"].IntegerValue in view_chosen_ids:
                        continue
                    if li[u"name"] in already_hidden_names or li[u"name"] in unhideable_names:
                        continue
                    try:
                        if not li[u"instance"].IsHidden(view):
                            still_visible_after.append(li[u"name"])
                    except Exception:
                        pass
                if still_visible_after:
                    newly_hidden_count -= len(still_visible_after)
                    w.append(
                        u"HideElements reported success but IsHidden still shows FALSE right "
                        u"after, for: {} — this points at a genuine API/Revit-version quirk, "
                        u"not a 'wrong view' explanation, since this check runs against the "
                        u"exact same view object the hide was just applied to.".format(
                            u", ".join(still_visible_after)))

            other_links_summary = u"Other links hidden: {} newly + {} already ({} total)".format(
                newly_hidden_count, len(already_hidden_names),
                newly_hidden_count + len(already_hidden_names))
            if unhideable_names:
                other_links_summary += u" — COULD NOT HIDE: {}".format(u", ".join(unhideable_names))
            if duplicate_link_names:
                other_links_summary += (
                    u"\nNOTE: these linked file(s) are placed as MORE THAN ONE separate "
                    u"instance in this model: {} — each instance is hidden/verified "
                    u"independently, so if the file still looks visible after this run, "
                    u"check whether every one of its instances actually got hidden.".format(
                        u", ".join(duplicate_link_names)))
            vr[u"other_links_summary"] = other_links_summary

            # FINAL re-verification, right before commit — proves whether
            # something later in THIS view's own transaction undid the
            # hide above.
            final_still_visible = []
            for li in links:
                if li[u"id"].IntegerValue in view_chosen_ids:
                    continue
                if li[u"name"] in unhideable_names:
                    continue
                try:
                    if not li[u"instance"].IsHidden(view):
                        final_still_visible.append(li[u"name"])
                except Exception:
                    pass
            if final_still_visible:
                w.append(
                    u"FINAL CHECK (right before commit): these non-selected link(s) show as "
                    u"NOT hidden even though Step 5 reported success earlier in this same "
                    u"run: {} — something later in this transaction undid the hide.".format(
                        u", ".join(sorted(set(final_still_visible)))))

            t.Commit()
        except Exception:
            t.RollBack()
            vr[u"error"] = traceback.format_exc()
        view_results.append(vr)

    # Deliberately NOT switching uidoc.ActiveView here — sheets are created
    # in the background and the user stays on whatever view they started on.

    def _names_preview(names, limit=15):
        # Named directly in the result dialog, not just a count — so
        # "N types found but nothing colored on this floor" is directly
        # checkable (search for these exact type names in the model)
        # instead of needing to dig into the pyRevit log.
        names = sorted(names)
        if not names:
            return u"(none)"
        shown = u", ".join(names[:limit])
        if len(names) > limit:
            shown += u", +{} more (see pyRevit log for the full list)".format(len(names) - limit)
        return shown

    header = (
        u"Coordination graphics applied to {} view(s): {}.\n\n"
        u"Architecture link(s): {}\nStructure link(s): {}\nTraffic link: {}\n\n"
        u"Architecture concrete types found: {} — {}\n"
        u"Structure concrete types found: {} — {}".format(
            len(view_results), u", ".join(vr[u"name"] for vr in view_results),
            u", ".join(li[u"name"] for li in arch_links),
            u", ".join(li[u"name"] for li in struct_links),
            traffic_link[u"name"] if (use_traffic and traffic_link) else u"(not used)",
            len(arch_names), _names_preview(arch_names),
            len(struct_names), _names_preview(struct_names))
    )

    per_view_blocks = []
    total_warnings = len(global_warnings)
    any_errors = False
    for vr in view_results:
        if vr[u"error"] is not None:
            any_errors = True
            per_view_blocks.append(
                u"— {} — FAILED, no changes made for this view:\n{}".format(
                    vr[u"name"], vr[u"error"]))
            continue
        sheet_line = (u"Sheet: {} — {}".format(vr[u"sheet"].SheetNumber, _elem_name(vr[u"sheet"]))
                      if vr[u"sheet"] is not None else u"Sheet: not created (see warnings)")
        block = u"— {} —\n{}\n{}\n{}".format(
            vr[u"name"], vr[u"other_links_summary"],
            vr[u"traffic_summary"] or u"Traffic link: (not used)", sheet_line)
        if vr[u"warnings"]:
            total_warnings += len(vr[u"warnings"])
            block += u"\n{} warning(s):\n{}".format(
                len(vr[u"warnings"]), u"\n".join(u"  • {}".format(x) for x in vr[u"warnings"]))
        per_view_blocks.append(block)

    body = header + u"\n\n" + u"\n\n".join(per_view_blocks)
    if global_warnings:
        body += u"\n\nShared setup warning(s):\n{}".format(
            u"\n".join(u"• {}".format(x) for x in global_warnings))

    title = u"EasyBIM — Coordination Graphics — Done"
    if any_errors:
        title = u"EasyBIM — Coordination Graphics — Done, with errors"
    elif total_warnings:
        title = u"EasyBIM — Coordination Graphics — Done, with warnings"
    TaskDialog.Show(title, body)

    # ── 2D mismatch export (BCF 2.1) ────────────────────────────────────────
    if all_mismatches:
        save_path = forms.save_file(file_ext=u"bcfzip", default_name=u"coordination_mismatches.bcfzip")
        if save_path:
            try:
                export_mismatches_to_bcf(all_mismatches, save_path)
                TaskDialog.Show(
                    u"EasyBIM — Coordination Graphics — BCF Export",
                    u"{} 2D mismatch(es) found across {} view(s) — exported to:\n{}".format(
                        len(all_mismatches), len(view_results), save_path))
            except Exception:
                TaskDialog.Show(
                    u"EasyBIM — Coordination Graphics — BCF Export Failed",
                    u"{} mismatch(es) were found, but the BCF export failed:\n\n{}".format(
                        len(all_mismatches), traceback.format_exc()))


def main():
    try:
        run()
    except Exception:
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics — Error",
            u"Unexpected error:\n\n{}".format(traceback.format_exc())
        )


main()
