# 剪映 4.3.1 草稿字段 × MCP 工具 覆盖率表

数据源：真实草稿 `<sample-draft>/draft_content.json`（应用自身重写过的权威样本）
**总计：228 个字段，已接入 143 个（62%）**


## segment（片段）（30 字段）

已接入 **27** · 底层/只读 3 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `cartoon` | set_segment_flags |
| `clip` | set_clip_transform |
| `common_keyframes` | set_keyframes(未验证) |
| `enable_adjust` | set_color_switches |
| `enable_color_curves` | set_color_switches |
| `enable_color_wheels` | set_color_switches |
| `enable_lut` | set_color_switches |
| `enable_smart_color_adjust` | set_color_switches |
| `extra_material_refs` | （底层/只读）|
| `group_id` | group_segments |
| `hdr_settings` | set_hdr(未验证) |
| `id` | （底层/只读）|
| `intensifies_audio` | set_segment_audio |
| `is_placeholder` | set_segment_flags |
| `is_tone_modify` | set_segment_flags |
| `keyframe_refs` | set_keyframes(未验证) |
| `last_nonzero_volume` | set_segment_audio |
| `material_id` | （底层/只读）|
| `render_index` | set_segment_layer |
| `reverse` | set_segment_speed |
| `source_timerange` | add_media_clip / set_segment_speed |
| `speed` | set_segment_speed |
| `target_timerange` | add_media_clip |
| `template_id` | set_raw_field(白名单) |
| `template_scene` | set_raw_field(白名单) |
| `track_attribute` | set_segment_flags |
| `track_render_index` | set_segment_layer |
| `uniform_scale` | set_clip_transform |
| `visible` | set_segment_layer |
| `volume` | set_segment_audio |

## materials.videos（42 字段）

已接入 **19** · 底层/只读 23 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `audio_fade` | set_segment_audio |
| `cartoon_path` | set_raw_field(白名单) |
| `category_id` | （底层/只读）|
| `category_name` | （底层/只读）|
| `check_flag` | （底层/只读）|
| `crop` | set_crop |
| `crop_ratio` | set_crop |
| `crop_scale` | set_crop |
| `duration` | add_transition_by_id / add_animation_by_id |
| `extra_type_option` | （底层/只读）|
| `formula_id` | （底层/只读）|
| `freeze` | set_raw_field(白名单) |
| `gameplay` | set_raw_field(白名单) |
| `has_audio` | （底层/只读）|
| `height` | （底层/只读）|
| `id` | （底层/只读）|
| `intensifies_audio_path` | set_raw_field(白名单) |
| `intensifies_path` | set_raw_field(白名单) |
| `is_ai_generate_content` | （底层/只读）|
| `is_unified_beauty_mode` | set_video_flags |
| `local_id` | （底层/只读）|
| `local_material_id` | （底层/只读）|
| `material_id` | （底层/只读）|
| `material_name` | （底层/只读）|
| `material_url` | （底层/只读）|
| `matting` | set_matting |
| `media_path` | （底层/只读）|
| `object_locked` | set_video_flags |
| `origin_material_id` | （底层/只读）|
| `path` | （底层/只读）|
| `picture_from` | （底层/只读）|
| `picture_set_category_id` | set_raw_field(白名单) |
| `picture_set_category_name` | set_raw_field(白名单) |
| `request_id` | （底层/只读）|
| `reverse_intensifies_path` | set_raw_field(白名单) |
| `reverse_path` | set_raw_field(白名单) |
| `source_platform` | （底层/只读）|
| `stable` | set_video_algorithm |
| `team_id` | （底层/只读）|
| `type` | （底层/只读）|
| `video_algorithm` | set_video_algorithm |
| `width` | （底层/只读）|

## materials.audios（27 字段）

已接入 **15** · 底层/只读 12 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `app_id` | （底层/只读）|
| `category_id` | （底层/只读）|
| `category_name` | （底层/只读）|
| `check_flag` | （底层/只读）|
| `duration` | add_transition_by_id / add_animation_by_id |
| `effect_id` | add_effect_by_id |
| `formula_id` | （底层/只读）|
| `id` | （底层/只读）|
| `intensifies_path` | set_raw_field(白名单) |
| `local_material_id` | （底层/只读）|
| `music_id` | set_raw_field(白名单) |
| `name` | add_effect_by_id |
| `path` | （底层/只读）|
| `request_id` | （底层/只读）|
| `resource_id` | add_effect_by_id |
| `source_platform` | （底层/只读）|
| `team_id` | （底层/只读）|
| `text_id` | set_raw_field(白名单) |
| `tone_category_id` | set_audio_tone |
| `tone_category_name` | set_audio_tone |
| `tone_effect_id` | set_audio_tone |
| `tone_effect_name` | set_audio_tone |
| `tone_speaker` | set_audio_tone |
| `tone_type` | set_audio_tone |
| `type` | （底层/只读）|
| `video_id` | set_raw_field(白名单) |
| `wave_points` | set_raw_field(白名单) |

## materials.texts（74 字段）

已接入 **64** · 底层/只读 10 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `add_type` | （底层/只读）|
| `alignment` | set_text_style |
| `background_alpha` | set_text_background |
| `background_color` | set_text_background |
| `background_height` | set_text_background |
| `background_horizontal_offset` | set_text_background |
| `background_round_radius` | set_text_background |
| `background_style` | set_text_background |
| `background_vertical_offset` | set_text_background |
| `background_width` | set_text_background |
| `bold_width` | set_text_style |
| `border_color` | set_text_style |
| `border_width` | set_text_style |
| `check_flag` | （底层/只读）|
| `content` | add_text_clip |
| `fixed_height` | set_text_layout |
| `fixed_width` | set_text_layout |
| `font_category_id` | （底层/只读）|
| `font_category_name` | （底层/只读）|
| `font_id` | （底层/只读）|
| `font_name` | set_text_font |
| `font_path` | set_text_font |
| `font_resource_id` | （底层/只读）|
| `font_size` | set_text_style |
| `font_source_platform` | set_raw_field(白名单) |
| `font_team_id` | set_raw_field(白名单) |
| `font_title` | （底层/只读）|
| `font_url` | set_raw_field(白名单) |
| `fonts` | set_raw_field(白名单) |
| `force_apply_line_max_width` | set_text_layout |
| `global_alpha` | set_text_layout |
| `group_id` | group_segments |
| `has_shadow` | set_text_shadow |
| `id` | （底层/只读）|
| `initial_scale` | set_raw_field(白名单) |
| `is_rich_text` | set_raw_field(白名单) |
| `italic_degree` | set_text_style |
| `ktv_color` | set_raw_field(白名单) |
| `language` | set_raw_field(白名单) |
| `layer_weight` | set_text_layout |
| `letter_spacing` | set_text_style |
| `line_spacing` | set_text_style |
| `name` | add_effect_by_id |
| `preset_category` | set_raw_field(白名单) |
| `preset_category_id` | set_raw_field(白名单) |
| `preset_has_set_alignment` | set_raw_field(白名单) |
| `preset_id` | set_raw_field(白名单) |
| `preset_index` | set_raw_field(白名单) |
| `preset_name` | set_raw_field(白名单) |
| `recognize_type` | set_raw_field(白名单) |
| `relevance_segment` | set_raw_field(白名单) |
| `shadow_alpha` | set_text_shadow |
| `shadow_angle` | set_text_shadow |
| `shadow_color` | set_text_shadow |
| `shadow_distance` | set_text_shadow |
| `shadow_point` | set_text_shadow |
| `shadow_smoothing` | set_text_shadow |
| `shape_clip_x` | set_raw_field(白名单) |
| `shape_clip_y` | set_raw_field(白名单) |
| `style_name` | set_raw_field(白名单) |
| `sub_type` | set_raw_field(白名单) |
| `text_alpha` | set_text_layout |
| `text_color` | set_text_style |
| `text_preset_resource_id` | set_raw_field(白名单) |
| `text_size` | （底层/只读）|
| `text_to_audio_ids` | set_raw_field(白名单) |
| `tts_auto_update` | set_raw_field(白名单) |
| `type` | （底层/只读）|
| `typesetting` | set_raw_field(白名单) |
| `underline` | set_text_style |
| `underline_offset` | set_raw_field(白名单) |
| `underline_width` | set_raw_field(白名单) |
| `use_effect_default_color` | set_raw_field(白名单) |
| `words` | set_raw_field(白名单) |

## materials.video_effects（21 字段）

已接入 **10** · 底层/只读 11 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `adjust_params` | set_adjust_values(需已有载体) |
| `apply_target_type` | （底层/只读）|
| `apply_time_range` | set_raw_field(白名单) |
| `category_id` | （底层/只读）|
| `category_name` | （底层/只读）|
| `common_keyframes` | set_keyframes(未验证) |
| `effect_id` | add_effect_by_id |
| `formula_id` | （底层/只读）|
| `id` | （底层/只读）|
| `name` | add_effect_by_id |
| `path` | （底层/只读）|
| `platform` | （底层/只读）|
| `render_index` | set_segment_layer |
| `request_id` | （底层/只读）|
| `resource_id` | add_effect_by_id |
| `source_platform` | （底层/只读）|
| `time_range` | set_raw_field(白名单) |
| `track_render_index` | set_segment_layer |
| `type` | （底层/只读）|
| `value` | add_effect_by_id |
| `version` | （底层/只读）|

## materials.transitions（12 字段）

已接入 **4** · 底层/只读 8 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `category_id` | （底层/只读）|
| `category_name` | （底层/只读）|
| `duration` | add_transition_by_id / add_animation_by_id |
| `effect_id` | add_effect_by_id |
| `id` | （底层/只读）|
| `is_overlap` | （底层/只读）|
| `name` | add_effect_by_id |
| `path` | （底层/只读）|
| `platform` | （底层/只读）|
| `request_id` | （底层/只读）|
| `resource_id` | add_effect_by_id |
| `type` | （底层/只读）|

## materials.material_animations（3 字段）

已接入 **1** · 底层/只读 2 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `animations` | add_animation_by_id |
| `id` | （底层/只读）|
| `type` | （底层/只读）|

## materials.speeds（5 字段）

已接入 **3** · 底层/只读 2 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `curve_speed` | set_segment_speed |
| `id` | （底层/只读）|
| `mode` | set_segment_speed |
| `speed` | set_segment_speed |
| `type` | （底层/只读）|

## materials.canvases（10 字段）

已接入 **0** · 底层/只读 10 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `album_image` | （底层/只读）|
| `blur` | （底层/只读）|
| `color` | （底层/只读）|
| `id` | （底层/只读）|
| `image` | （底层/只读）|
| `image_id` | （底层/只读）|
| `image_name` | （底层/只读）|
| `source_platform` | （底层/只读）|
| `team_id` | （底层/只读）|
| `type` | （底层/只读）|

## materials.sound_channel_mappings（4 字段）

已接入 **0** · 底层/只读 4 · **未接入 0**

| 字段 | 工具 |
|---|---|
| `audio_channel_mapping` | （底层/只读）|
| `id` | （底层/只读）|
| `is_config_open` | （底层/只读）|
| `type` | （底层/只读）|
