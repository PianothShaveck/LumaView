"""Small, versioned migrations of remembered UI defaults."""
import json


def upgrade_settings(settings):
    if settings.value('migrations/adaptive-tiff-012', False, type=bool):
        return

    def upgrade(options):
        if (isinstance(options, dict) and options.get('tiff_bilevel_method') == 'compact'
                and options.get('tiff_bilevel_contrast', 3.0) == 3.0):
            options['tiff_bilevel_method'] = 'adaptive'
            options['tiff_adaptive_alpha_background'] = False

    # Named presets and custom contrast settings retain their explicit choices.
    for key in ('format/TIFF', 'batch/options'):
        try:
            value = json.loads(str(settings.value(key, '{}')))
            if not isinstance(value, dict):
                continue
            if key == 'batch/options':
                formats = value.get('format_options', {})
                if isinstance(formats, dict):
                    upgrade(formats.get('TIFF'))
            else:
                upgrade(value)
            settings.setValue(key, json.dumps(value))
        except (TypeError, ValueError):
            continue
    settings.setValue('migrations/adaptive-tiff-012', True)
