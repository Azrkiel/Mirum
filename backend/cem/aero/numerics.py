"""Damped root solves inside a supplied evidence domain; no extrapolation."""
import numpy as np

from .polars import PolarCoverageError


class NumericalConvergenceError(ValueError):
    """No covered numerical root was established; not proof of physical failure."""


def bounded_newton(function, initial, *, tolerance=1e-9, iterations=40):
    """Only evaluate accepted-domain residuals; reject invalid trial steps.

    The domain is defined by PolarCoverageError. A trial outside it supplies no
    coefficient or fabricated penalty. A finite-difference Jacobian and Armijo
    backtracking keep all accepted iterates covered. Convergence requires the
    actual residual, never merely a small step or a constrained minimum.
    """
    x=np.asarray(initial,dtype=float).copy()
    if x.ndim!=1 or not 1<=len(x)<=256 or not np.all(np.isfinite(x)):
        raise NumericalConvergenceError('Nonfinite or invalid numerical initial state')
    rejected=0

    def evaluate(value):
        result=np.asarray(function(value),dtype=float)
        if result.shape!=x.shape or not np.all(np.isfinite(result)):
            raise NumericalConvergenceError('Nonfinite or invalid numerical residual')
        return result

    try:
        residual=evaluate(x)
    except PolarCoverageError as exc:
        raise NumericalConvergenceError('Initial root residual has no covered evaluation: '+str(exc)) from exc
    for iteration in range(iterations+1):
        norm=float(np.max(np.abs(residual)))
        if norm<=tolerance:
            return dict(value=x,residual_norm=norm,iterations=iteration,rejected_domain_trials=rejected)
        if iteration==iterations:
            break
        jacobian=np.empty((len(x),len(x)))
        for j in range(len(x)):
            step=1e-5*max(1.,abs(x[j]))
            for direction in (1.,-1.):
                trial=x.copy();trial[j]+=direction*step
                try:
                    sample=evaluate(trial)
                except PolarCoverageError:
                    rejected+=1
                    continue
                jacobian[:,j]=(sample-residual)/(direction*step)
                break
            else:
                raise NumericalConvergenceError('No covered finite difference for root Jacobian')
        try:
            change=np.linalg.solve(jacobian,-residual)
        except np.linalg.LinAlgError as exc:
            raise NumericalConvergenceError('Singular root Jacobian') from exc
        if not np.all(np.isfinite(change)):
            raise NumericalConvergenceError('Nonfinite root step')
        merit=float(residual@residual)
        for exponent in range(20):
            fraction=2.**(-exponent)
            trial=x+fraction*change
            try:
                sample=evaluate(trial)
            except PolarCoverageError:
                rejected+=1
                continue
            if float(sample@sample)<merit*(1-1e-4*fraction):
                x,residual=trial,sample
                break
        else:
            raise NumericalConvergenceError('No improving covered root step; no equilibrium established')
    raise NumericalConvergenceError(f'Covered root did not converge in {iterations} iterations')
