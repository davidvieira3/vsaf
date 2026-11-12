import csv
import io

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .grids import GRIDS, filtered_queryset

GIB = 1024**3


def _value(col, v):
    if v is None:
        return None
    if col.type == "bytes":
        return round(v / GIB, 1)
    if col.type == "mb":
        return round(v / 1024, 1)
    if col.type == "datetime":
        return timezone.localtime(v).replace(tzinfo=None)
    if col.type == "bool":
        return "Oui" if v else "Non"
    return v


def _header(col):
    return f"{col.title} (Gio)" if col.type in ("bytes", "mb") else col.title


def _rows(grid, params):
    qs = filtered_queryset(grid, params).values_list(*grid.fields)
    for row in qs.iterator(chunk_size=2000):
        yield [_value(c, v) for c, v in zip(grid.columns, row)]


class _Echo:
    def write(self, value):
        return value


def export_csv(grid, params):
    writer = csv.writer(_Echo(), delimiter=";")

    def stream():
        yield "\ufeff"
        yield writer.writerow([_header(c) for c in grid.columns])
        for row in _rows(grid, params):
            yield writer.writerow(["" if v is None else (f"{v}".replace(".", ",") if isinstance(v, float) else v) for v in row])

    resp = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{grid.key}-{timezone.now():%Y%m%d-%H%M}.csv"'
    return resp


def _sheet(wb, grid, params, title=None):
    """Feuille en mode « write-only » : mémoire constante même avec des dizaines de milliers de VM."""
    ws = wb.create_sheet(title=(title or grid.title)[:31])
    for i, c in enumerate(grid.columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(45, (c.width or 120) // 7))
    try:
        ws.freeze_panes = "B2"
    except AttributeError:
        pass
    ws.append([_header(c) for c in grid.columns])
    n = 1
    for row in _rows(grid, params):
        ws.append(row)
        n += 1
    try:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(grid.columns))}{n}"
    except AttributeError:
        pass
    return ws


def _xlsx_response(wb, name):
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}-{timezone.now():%Y%m%d-%H%M}.xlsx"'
    return resp


def new_workbook():
    return Workbook(write_only=True)


def export_xlsx(grid, params):
    wb = new_workbook()
    _sheet(wb, grid, params)
    return _xlsx_response(wb, grid.key)


INVENTORY_SHEETS = [("sources", "vSource"), ("clusters", "vCluster"), ("hosts", "vHost"), ("datastores", "vDatastore"),
                    ("switches", "vSwitch"), ("portgroups", "vPort"), ("neighbors", "vNetwork CDP-LLDP"), ("vms", "vInfo")]
STORAGE_SHEETS = [("arrays", "Baies"), ("pools", "Pools-Agrégats"), ("volumes", "Volumes-LUN")]
SHEETS = INVENTORY_SHEETS + STORAGE_SHEETS


def build_workbook(params, sheets=SHEETS):
    wb = new_workbook()
    for key, title in sheets:
        _sheet(wb, GRIDS[key], params, title)
    return wb


def export_workbook(params):
    """Classeur complet façon RVTools, un onglet par vue, filtré par plateforme / source si demandé."""
    return _xlsx_response(build_workbook(params), "inventaire")
