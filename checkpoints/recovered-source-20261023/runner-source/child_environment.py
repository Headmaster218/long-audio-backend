"""Only the fully pinned product root propagates to normal native grandchildren."""
from pathlib import Path


def validate_product_pythonpath(environment, product_source):
    expected = str(Path(product_source))
    present = environment.get('PYTHONPATH')
    if present is not None and present != expected:
        raise ValueError('ambient or different product PYTHONPATH')
    if environment.get('PYTHONHOME'):
        raise ValueError('ambient PYTHONHOME')


def install_product_path(environment, product_source):
    """Call only AFTER full manifest verification, inside the service process.

    No startup import allowlist expansion. Only future normal native children
    inherit this exact bound root; -I probe children keep their isolation.
    """
    source = Path(product_source)
    if not source.is_absolute() or source.resolve(strict=True) != source:
        raise ValueError('product source must be canonical')
    validate_product_pythonpath(environment, source)
    environment['PYTHONPATH'] = str(source)
