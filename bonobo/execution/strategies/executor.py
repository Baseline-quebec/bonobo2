import asyncio
import functools
import logging
import sys
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor

from cached_property import cached_property

from bonobo import settings
from bonobo.constants import BEGIN, END
from bonobo.execution.contexts.graph import AsyncGraphExecutionContext
from bonobo.execution.strategies.base import Strategy
from bonobo.util import get_name

logger = logging.getLogger(__name__)


class ExecutorStrategy(Strategy):
    """
    Strategy based on a concurrent.futures.Executor subclass (or similar interface).

    """

    executor_factory = Executor

    def create_executor(self, graph):
        return self.executor_factory()

    def execute(self, graph, **kwargs):
        context = self.create_graph_execution_context(graph, **kwargs)
        context.write(BEGIN, (), END)

        futures = []

        with self.create_executor(graph) as executor:
            try:
                context.start(self.get_starter(executor, futures))
            except Exception:
                logger.critical(
                    "Exception caught while starting execution context.",
                    exc_info=sys.exc_info(),
                )

            while context.alive:
                try:
                    context.tick()
                except KeyboardInterrupt:
                    logging.getLogger(__name__).warning(
                        "KeyboardInterrupt received. Trying to terminate the nodes gracefully."
                    )
                    context.kill()
                    break

            context.stop()

        return context

    def get_starter(self, executor, futures):
        def starter(node):
            @functools.wraps(node)
            def _runner():
                try:
                    with node:
                        node.loop()
                except Exception:
                    logging.getLogger(__name__).critical(
                        "Critical error in threadpool node starter.",
                        exc_info=sys.exc_info(),
                    )

            try:
                futures.append(executor.submit(_runner))
            except Exception:
                logging.getLogger(__name__).critical(
                    "futures.append", exc_info=sys.exc_info()
                )

        return starter


class ThreadPoolExecutorStrategy(ExecutorStrategy):
    executor_factory = ThreadPoolExecutor

    def create_executor(self, graph):
        return self.executor_factory(max_workers=len(graph))


class AsyncThreadPoolExecutorStrategy(ThreadPoolExecutorStrategy):
    GraphExecutionContextType = AsyncGraphExecutionContext

    def __init__(self, GraphExecutionContextType=None):
        if not settings.ALPHA.get():
            raise NotImplementedError(
                "{} is experimental, you need to explicitely activate it using ALPHA=True in system env.".format(
                    get_name(self)
                )
            )
        super().__init__(GraphExecutionContextType)

    @cached_property
    def loop(self):
        return asyncio.get_event_loop()

    def create_graph_execution_context(self, *args, **kwargs):
        return super(
            AsyncThreadPoolExecutorStrategy, self
        ).create_graph_execution_context(*args, **kwargs, loop=self.loop)

    def get_starter(self, executor, futures):
        return functools.partial(
            self.loop.run_in_executor,
            executor,
            super(AsyncThreadPoolExecutorStrategy, self).get_starter(executor, futures),
        )


class ProcessPoolExecutorStrategy(ExecutorStrategy):
    executor_factory = ProcessPoolExecutor

    def create_executor(self, graph):
        return self.executor_factory(max_workers=len(graph))
