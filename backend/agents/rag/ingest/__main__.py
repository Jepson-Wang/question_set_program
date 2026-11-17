"""
命令行入库。

    python -m backend.agents.rag.ingest file <文件或目录>... [--grade 七年级]
    python -m backend.agents.rag.ingest generate --kp 一元一次方程 --grade 七年级 [--difficulty 中等] [-n 10]

注意：Chroma 的本地库不支持多进程同时写入。服务运行期间请改用后台接口入库；
命令行入库完成后要重启服务，服务里的词法索引才会包含新题。
"""
import argparse
import asyncio
import sys
from functools import partial
from pathlib import Path

from backend.agents.rag.ingest.judge import JudgeConfigError
from backend.agents.rag.ingest.loaders import SUPPORTED_SUFFIXES, UnsupportedDocumentError
from backend.agents.rag.ingest.service import IngestReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.agents.rag.ingest", description="RAG 知识库入库")
    sub = parser.add_subparsers(dest="command", required=True)

    file_cmd = sub.add_parser("file", help="导入 PDF / Word 文件或目录")
    file_cmd.add_argument("paths", nargs="+", type=Path)
    file_cmd.add_argument("--grade", default=None, help="年级提示，如 七年级")

    gen_cmd = sub.add_parser("generate", help="按知识点生成种子题")
    gen_cmd.add_argument("--kp", required=True, help="知识点，如 一元一次方程")
    gen_cmd.add_argument("--grade", required=True, help="年级，如 七年级")
    # 难度限定取值：模型侧的难度枚举就是这三个，放宽只会让脏数据进库
    gen_cmd.add_argument("--difficulty", default="中等", choices=["简单", "中等", "困难"])
    gen_cmd.add_argument("-n", type=int, default=10, help="生成数量")
    return parser


def expand_paths(paths) -> list[Path]:
    """目录展开为其中（递归）所有支持的文件并排序；单个文件原样保留。"""
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES))
        else:
            result.append(path)
    return result


def format_report(report: IngestReport) -> str:
    text = (f"抽取 {report.extracted}，精确重复 {report.exact_duplicates}，近似重复 {report.near_duplicates}，"
            f"通过 {report.passed}，进隔离区 {report.rejected}；讲解新增 {report.knowledge_added}")
    if report.errors:
        text += f"；错误 {len(report.errors)} 条"
    return text


async def run(args, runtime=None, quarantine=None, out=print) -> int:
    if runtime is None:
        from backend.agents.rag.runtime import get_rag_runtime
        runtime = get_rag_runtime()
    try:
        # 先确认闸门可用再动手：裁判没配好时早退，
        # 免得白跑一遍模型，最后才发现题全部进不了库。
        runtime.ensure_judge()
    except JudgeConfigError as e:
        out(f"无法入库：{e}")
        return 2

    db_engine = None
    if quarantine is None:
        # 命令行没有服务的启动钩子，自己建表、连库
        import backend.model.rag as rag_models
        from backend.dao.rag_mapper import RagQuarantineMapper
        from backend.model import AsyncSessionLocal, engine

        async with engine.begin() as conn:
            # 引用 rag_models.Base：既保证隔离区表的模型已注册，又保证建的是同一份元数据
            await conn.run_sync(rag_models.Base.metadata.create_all)
        quarantine = partial(RagQuarantineMapper(AsyncSessionLocal).put, job_id=None)
        db_engine = engine

    try:
        await runtime.warm_up()
        service = runtime.ingest_service(quarantine)
        total = IngestReport()
        if args.command == "file":
            for path in expand_paths(args.paths):
                out(f"入库：{path}")
                try:
                    report = await service.ingest_file(path, grade_hint=args.grade)
                except UnsupportedDocumentError as e:
                    out(f"  跳过：{e}")
                    continue
                except Exception as e:  # 一个文件出错不应中断整批
                    out(f"  失败：{type(e).__name__}: {e}")
                    continue
                out("  " + format_report(report))
                for err in report.errors:
                    out(f"  ! {err}")
                total.merge(report)
        else:
            total = await service.generate_and_ingest(args.kp, args.grade, args.difficulty, args.n)
            for err in total.errors:
                out(f"  ! {err}")
        out("合计：" + format_report(total))
        return 0
    finally:
        if db_engine is not None:
            # 命令行是一次性进程：退出前收掉连接池，避免进程挂住不退出
            await db_engine.dispose()


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
