{% extends "report_base.tpl" %}

{% block subject_information %}
<h3 class="mt-4">Anatomical Processing Summary (DLMUSE)</h3>
<p>
    This report summarizes the anatomical processing performed by the NiChart DLMUSE workflow.
    Segmentation and brain masking were performed using the DLMUSE model.
    Brain volumes were estimated from the segmentation.
</p>
{% endblock %}

{% block methods %}
<!-- Add specific methods description for DLMUSE anatomical part if needed -->
{% endblock %}

{% block after_coreg %}

<h4>Segmentation and Brain Masking</h4>
<p>
    The following images show the results of brain extraction and segmentation overlaid on the preprocessed T1w image.
</p>
{{ reportlets['segmentation_plot'] }}

<h4>Brain Volume Estimates</h4>
<p>
    The table below shows the estimated volumes (in mm³) for various brain structures derived from the DLMUSE segmentation.
</p>
{{ reportlets['volumes_table'] }}

{% endblock %} 