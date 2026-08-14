from typing import Any, Dict, List, Optional, Sequence, Tuple

import wx

from .async_task import AsyncTaskRunner
from .models import Datasource
from .repositories import DatasourceRepository


def _fmt(value: Any) -> str:
    return "?" if value is None else str(value)


def _fmt_num(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return _fmt(value)


def _fmt_bytes(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{int(value):,} bytes"
    return _fmt(value)


def _fmt_bool(value: Any) -> str:
    if value is None:
        return "?"
    return "Yes" if str(value) in ("1", "true", "True") else "No"


class StatsView(wx.Panel):
    """Server diagnostics for the connected datasource - built from a
    single `client.info("everything")` call (see
    DatasourceRepository.get_server_info) covering server/memory/cpu/
    clients/persistence/stats/replication/keyspace/commandstats/
    errorstats/latencystats/cluster/modules in one round-trip. Deliberately
    doesn't add SLOWLOG/MEMORY STATS/CLUSTER INFO calls here - those are
    separate, heavier commands better suited to their own on-demand
    section later, mirroring the cheap-list/lazy-detail split used
    elsewhere (see IndexesView/IndexDetailsDialog)."""

    def __init__(self, parent: wx.Window, repository: DatasourceRepository) -> None:
        super().__init__(parent)
        self._repository = repository
        self._datasource: Optional[Datasource] = None
        self._async = AsyncTaskRunner(self)

        outer = wx.BoxSizer(wx.VERTICAL)

        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_btn = wx.Button(self, label="Refresh")
        toolbar.Add(self._refresh_btn, 0, wx.RIGHT, 8)
        self._status = wx.StaticText(self, label="")
        toolbar.Add(self._status, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(toolbar, 0, wx.EXPAND | wx.ALL, 12)

        self._scroller = wx.ScrolledWindow(self, style=wx.BORDER_SUNKEN)
        self._scroller.SetScrollRate(0, 20)
        self._body = wx.BoxSizer(wx.VERTICAL)
        self._scroller.SetSizer(self._body)
        outer.Add(self._scroller, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(outer)

        self._refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)

    # ------------------------------------------------------------------
    # Datasource lifecycle
    # ------------------------------------------------------------------
    def set_datasource(self, datasource: Datasource) -> None:
        self._datasource = datasource
        self.reload()

    def clear(self) -> None:
        self._datasource = None
        self._status.SetLabel("")
        self._render_message("")

    def _on_refresh(self, event: wx.CommandEvent) -> None:
        self.reload()

    def reload(self) -> None:
        if self._datasource is None:
            return
        datasource = self._datasource
        self._status.SetLabel("Loading...")
        self._render_message("Loading...")

        def on_success(info: dict) -> None:
            version = info.get("redis_version", "?")
            uptime = info.get("uptime_in_days", "?")
            self._status.SetLabel(f"Redis {version} - up {uptime}d")
            self._render(info)

        def on_error(exc: Exception) -> None:
            self._status.SetLabel("Could not load stats")
            self._render_message(f"Could not load stats:\n\n{exc}")

        self._async.run(
            work=lambda: self._repository.get_server_info(datasource),
            on_success=on_success,
            on_error=on_error,
            disable=[self._refresh_btn],
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_message(self, text: str) -> None:
        self._body.Clear(delete_windows=True)
        if text:
            self._body.Add(wx.StaticText(self._scroller, label=text), 0, wx.ALL, 12)
        self._scroller.FitInside()
        self._scroller.Layout()

    def _render(self, info: Dict[str, Any]) -> None:
        self._body.Clear(delete_windows=True)

        self._add_section(
            "Server",
            [
                ("Version", info.get("redis_version")),
                ("Mode", info.get("redis_mode")),
                ("OS", info.get("os")),
                ("Uptime", f"{info.get('uptime_in_days', '?')}d ({_fmt_num(info.get('uptime_in_seconds'))}s)"),
                ("TCP port", info.get("tcp_port")),
            ],
        )

        self._add_section(
            "Memory",
            [
                ("Used memory", info.get("used_memory_human")),
                ("Used memory (RSS)", info.get("used_memory_rss_human")),
                ("Fragmentation ratio", info.get("mem_fragmentation_ratio")),
                ("Peak memory", info.get("used_memory_peak_human")),
                ("Peak %", info.get("used_memory_peak_perc")),
                ("Max memory", info.get("maxmemory_human") or "0B (unlimited)"),
                ("Eviction policy", info.get("maxmemory_policy")),
                ("Lua/scripts memory", info.get("used_memory_lua_human")),
                ("Functions memory", _fmt_bytes(info.get("used_memory_functions"))),
                ("Replication buffers", _fmt_bytes(info.get("mem_total_replication_buffers"))),
                ("Client memory (normal)", _fmt_bytes(info.get("mem_clients_normal"))),
                ("Client memory (replicas)", _fmt_bytes(info.get("mem_clients_slaves"))),
                ("Lazy-free pending", _fmt_num(info.get("lazyfree_pending_objects"))),
            ],
        )

        self._add_section(
            "CPU",
            [
                ("Sys CPU (total)", info.get("used_cpu_sys")),
                ("User CPU (total)", info.get("used_cpu_user")),
                ("Sys CPU (main thread)", info.get("used_cpu_sys_main_thread")),
                ("User CPU (main thread)", info.get("used_cpu_user_main_thread")),
            ],
        )

        self._add_section(
            "Clients",
            [
                ("Connected", f"{_fmt_num(info.get('connected_clients'))} / {_fmt_num(info.get('maxclients'))}"),
                ("Blocked", _fmt_num(info.get("blocked_clients"))),
                ("Tracking (client-side cache)", _fmt_num(info.get("tracking_clients"))),
                ("Pub/Sub", _fmt_num(info.get("pubsub_clients"))),
                ("Watching (WATCH)", _fmt_num(info.get("watching_clients"))),
                ("Max input buffer (recent)", _fmt_bytes(info.get("client_recent_max_input_buffer"))),
                ("Max output buffer (recent)", _fmt_bytes(info.get("client_recent_max_output_buffer"))),
            ],
        )

        self._add_section(
            "Persistence",
            [
                ("Changes since last save", _fmt_num(info.get("rdb_changes_since_last_save"))),
                ("BGSAVE in progress", _fmt_bool(info.get("rdb_bgsave_in_progress"))),
                ("Last BGSAVE status", info.get("rdb_last_bgsave_status")),
                ("Last BGSAVE duration", f"{_fmt(info.get('rdb_last_bgsave_time_sec'))}s"),
                ("AOF enabled", _fmt_bool(info.get("aof_enabled"))),
                ("AOF rewrite in progress", _fmt_bool(info.get("aof_rewrite_in_progress"))),
                ("Last AOF rewrite status", info.get("aof_last_bgrewrite_status")),
                ("Last AOF write status", info.get("aof_last_write_status")),
                ("Current fork %", info.get("current_fork_perc")),
                ("Current fork CoW size", _fmt_bytes(info.get("current_cow_size"))),
                ("Last fork duration", f"{_fmt_num(info.get('latest_fork_usec'))} usec"),
            ],
        )

        hits = info.get("keyspace_hits")
        misses = info.get("keyspace_misses")
        total = (hits or 0) + (misses or 0)
        hit_ratio = f"{hits / total * 100:.1f}%" if total else "n/a"
        self._add_section(
            "Throughput",
            [
                ("Ops/sec (instantaneous)", _fmt_num(info.get("instantaneous_ops_per_sec"))),
                ("Total commands processed", _fmt_num(info.get("total_commands_processed"))),
                ("Total connections received", _fmt_num(info.get("total_connections_received"))),
                ("Rejected connections", _fmt_num(info.get("rejected_connections"))),
                ("Input / Output (KB/s)", f"{_fmt(info.get('instantaneous_input_kbps'))} / {_fmt(info.get('instantaneous_output_kbps'))}"),
                ("Keyspace hit ratio", f"{hit_ratio} ({_fmt_num(hits)} hits / {_fmt_num(misses)} misses)"),
                ("Expired keys", _fmt_num(info.get("expired_keys"))),
                ("Evicted keys", _fmt_num(info.get("evicted_keys"))),
                ("Total error replies", _fmt_num(info.get("total_error_replies"))),
                ("Eventloop cycles", _fmt_num(info.get("eventloop_cycles"))),
                ("Eventloop duration (instantaneous)", f"{_fmt_num(info.get('instantaneous_eventloop_duration_usec'))} usec"),
            ],
        )

        replication_rows = [
            ("Role", info.get("role")),
            ("Connected replicas", _fmt_num(info.get("connected_slaves"))),
            ("Master repl offset", _fmt_num(info.get("master_repl_offset"))),
            ("Full syncs", _fmt_num(info.get("sync_full"))),
            ("Partial syncs (ok / err)", f"{_fmt_num(info.get('sync_partial_ok'))} / {_fmt_num(info.get('sync_partial_err'))}"),
            ("Repl backlog (size / used)", f"{_fmt_bytes(info.get('repl_backlog_size'))} / {_fmt_num(info.get('repl_backlog_histlen'))}"),
        ]
        if info.get("role") == "slave":
            replication_rows.extend(
                [
                    ("Master host", f"{info.get('master_host', '?')}:{info.get('master_port', '?')}"),
                    ("Master link status", info.get("master_link_status")),
                    ("Master last I/O", f"{_fmt_num(info.get('master_last_io_seconds_ago'))}s ago"),
                    ("Replica read-only", _fmt_bool(info.get("slave_read_only"))),
                ]
            )
        self._add_section("Replication", replication_rows)

        replica_rows = [
            (name, r.get("ip", "?"), r.get("port", "?"), r.get("state", "?"), _fmt_num(r.get("offset")), _fmt_num(r.get("lag")))
            for name, r in self._numbered_entries(info, "slave")
        ]
        if replica_rows:
            self._add_table(
                "Connected Replicas",
                [("Name", 80), ("IP", 140), ("Port", 70), ("State", 90), ("Offset", 100), ("Lag", 80)],
                replica_rows,
            )

        if str(info.get("cluster_enabled")) == "1":
            self._add_section(
                "Cluster",
                [
                    ("State", info.get("cluster_state")),
                    ("Known nodes", _fmt_num(info.get("cluster_known_nodes"))),
                    ("Size", _fmt_num(info.get("cluster_size"))),
                    ("Slots assigned", _fmt_num(info.get("cluster_slots_assigned"))),
                    ("Slots ok / fail / pfail", f"{_fmt_num(info.get('cluster_slots_ok'))} / {_fmt_num(info.get('cluster_slots_fail'))} / {_fmt_num(info.get('cluster_slots_pfail'))}"),
                ],
            )

        db_rows = [
            (name, _fmt_num(db.get("keys")), _fmt_num(db.get("expires")), _fmt_num(db.get("avg_ttl")))
            for name, db in self._numbered_entries(info, "db")
        ]
        self._add_table("Keyspace", [("Database", 90), ("Keys", 100), ("With TTL", 100), ("Avg TTL (ms)", 120)], db_rows)

        module_rows = [(m.get("name", "?"), m.get("ver", "?")) for m in (info.get("modules") or [])]
        self._add_table("Modules", [("Name", 160), ("Version", 100)], module_rows)

        command_rows = self._command_stat_rows(info)
        self._add_table(
            "Command Stats (sorted by total time, from this connection's session)",
            [
                ("Command", 140),
                ("Calls", 80),
                ("Total usec", 100),
                ("Usec/call", 90),
                ("Failed", 70),
                ("Rejected", 70),
                ("p50 usec", 80),
                ("p99 usec", 80),
                ("p99.9 usec", 80),
            ],
            command_rows,
        )

        error_rows = sorted(
            (
                (name[len("errorstat_"):], _fmt_num(value.get("count")))
                for name, value in info.items()
                if name.startswith("errorstat_") and isinstance(value, dict)
            ),
            key=lambda row: row[0],
        )
        self._add_table("Error Stats", [("Error code", 140), ("Count", 100)], error_rows, last=True)

        self._scroller.FitInside()
        self._scroller.Layout()

    @staticmethod
    def _numbered_entries(info: Dict[str, Any], prefix: str) -> List[Tuple[str, dict]]:
        """dbN / slaveN entries - INFO reply keys like `db0`/`slave0`,
        already parsed by redis-py into a dict per entry (see
        DatasourceRepository.get_server_info)."""
        entries = [
            (key, value)
            for key, value in info.items()
            if key.startswith(prefix) and key[len(prefix):].isdigit() and isinstance(value, dict)
        ]
        entries.sort(key=lambda entry: entry[0])
        return entries

    @staticmethod
    def _command_stat_rows(info: Dict[str, Any]) -> List[Tuple[str, ...]]:
        names = [key[len("cmdstat_"):] for key in info if key.startswith("cmdstat_")]
        names.sort(key=lambda name: info[f"cmdstat_{name}"].get("usec", 0) or 0, reverse=True)
        rows = []
        for name in names:
            stats = info.get(f"cmdstat_{name}", {})
            latency = info.get(f"latency_percentiles_usec_{name}", {})
            rows.append(
                (
                    name,
                    _fmt_num(stats.get("calls")),
                    _fmt_num(stats.get("usec")),
                    _fmt(stats.get("usec_per_call")),
                    _fmt_num(stats.get("failed_calls")),
                    _fmt_num(stats.get("rejected_calls")),
                    _fmt(latency.get("p50")),
                    _fmt(latency.get("p99")),
                    _fmt(latency.get("p99.9")),
                )
            )
        return rows

    # ------------------------------------------------------------------
    # Section/table builders
    # ------------------------------------------------------------------
    def _add_section(self, title: str, rows: Sequence[Tuple[str, Any]]) -> None:
        self._body.Add(self._section_header(title), 0, wx.TOP | wx.LEFT | wx.RIGHT, 12)

        grid = wx.FlexGridSizer(cols=4, gap=(12, 4))
        grid.AddGrowableCol(1)
        grid.AddGrowableCol(3)
        for label, value in rows:
            grid.Add(wx.StaticText(self._scroller, label=label), 0)
            grid.Add(wx.StaticText(self._scroller, label=_fmt(value)), 0, wx.EXPAND)
        self._body.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        self._body.Add(wx.StaticLine(self._scroller), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

    def _add_table(
        self,
        title: str,
        columns: Sequence[Tuple[str, int]],
        rows: Sequence[Tuple[Any, ...]],
        last: bool = False,
    ) -> None:
        self._body.Add(self._section_header(title), 0, wx.TOP | wx.LEFT | wx.RIGHT, 12)
        if not rows:
            self._body.Add(wx.StaticText(self._scroller, label="(none)"), 0, wx.ALL, 12)
        else:
            height = min(30 + 20 * len(rows), 260)
            list_ctrl = wx.ListCtrl(self._scroller, style=wx.LC_REPORT | wx.BORDER_SUNKEN, size=(-1, height))
            for column, (label, width) in enumerate(columns):
                list_ctrl.InsertColumn(column, label, width=width)
            for row_index, row in enumerate(rows):
                list_ctrl.InsertItem(row_index, str(row[0]))
                for column in range(1, len(row)):
                    list_ctrl.SetItem(row_index, column, str(row[column]))
            self._body.Add(list_ctrl, 0, wx.EXPAND | wx.ALL, 12)
        if not last:
            self._body.Add(wx.StaticLine(self._scroller), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

    def _section_header(self, title: str) -> wx.StaticText:
        header = wx.StaticText(self._scroller, label=title)
        font = header.GetFont()
        font.MakeBold()
        header.SetFont(font)
        return header
