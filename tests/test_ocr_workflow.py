"""Source-image workflow contracts; fixture responses are not an OCR quality claim."""
from pathlib import Path
import copy
import io
import tempfile
import unittest

import pymupdf
from PIL import Image

from scripts.document_ir import (ContractError, atomic_write_json, atomic_write_jsonl,
    canonical_hash, create_job, file_hash, load_job, read_json, read_jsonl)
from scripts.prepare_ocr import BINDINGS, collect_ocr, prepare_ocr
from scripts.backends.manual_overlay import apply_overlay
from scripts.backends.pdf_order import order_pdf_units


class TestOCRWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='OCR 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'scan.pdf'
        doc = pymupdf.open()
        regions, items = [], []
        for n, color in enumerate(('red', 'blue'), 1):
            p = doc.new_page(width=200, height=200)
            p.insert_text((10, 15), 'Header')
            stream = io.BytesIO()
            Image.new('RGB', (200, 200), color).save(stream, format='PNG')
            p.insert_image([10, 30, 190, 190], stream=stream.getvalue())
            items.append({'id':f'h{n}', 'page':n, 'source':'Header', 'kind':'sentence'})
            regions.append({'region_id':f'p{n}:image', 'locator':{'page':n,'bbox':[10,30,190,190],
                'width':200,'height':200,'coordinate_system':'unrotated_top_left_pt'},
                'role':'image','disposition':'needs_review','raw_text':'','unit_ids':[]})
        doc.save(self.pdf)
        doc.close()
        self.regions = regions
        self.job = create_job(self.pdf, self.root/'job', items, regions=regions)
        self.manifest = prepare_ocr(self.job['root'], dpi=144)
        self.responses = []
        for index, pointer in enumerate(self.manifest['regions']):
            request = read_json(self.job['root']/pointer['request'])
            self.responses.append({'region_id':request['region_id'], **{k:request[k] for k in BINDINGS},
                'status':'transcribed', 'complete':True,
                'producer':{'name':'fixture transcriber','method':'agent'},
                'units':[{'source':'Visible fixture text.','kind':'sentence','bbox':[20,40,180,70],
                          'order':0,'uncertain':False}], 'uncertainties':[]})

    def reviews(self):
        return [{'region_id':r['region_id'], **{k:r[k] for k in BINDINGS},
                 'transcription_hash':canonical_hash(r),'independent':True,
                 'reviewer':{'name':'separate fixture reviewer','method':'agent'},
                 'status':'PASS','findings':[],'reason':'Synthetic source-image fixture assessment'}
                for r in self.responses]

    def write(self, reviews=None):
        atomic_write_jsonl(self.job['root']/'ocr/responses.jsonl', self.responses)
        atomic_write_jsonl(self.job['root']/'ocr/reviews.jsonl', reviews if reviews is not None else self.reviews())

    def collect(self):
        return collect_ocr(self.job['root'], self.root/'overlay.json')

    def create_compiled_source_job(self):
        """Freeze a PDF together with the project and compile provenance."""
        source = self.root / 'source/main.tex'
        source.parent.mkdir()
        source.write_text(r'\documentclass{article}\begin{document}Source.\end{document}', encoding='utf-8')
        log = self.root / 'compile.log'
        log.write_text('Synthetic compile fixture; no compiler run.', encoding='utf-8')
        manifest = self.root / 'compile-manifest.json'
        atomic_write_json(manifest, {'source_sha256': file_hash(source), 'pdf_sha256': file_hash(self.pdf)})
        raw_regions = copy.deepcopy(self.regions)
        pdf_hash = file_hash(self.pdf)
        # Native regions begin with the PDF identity; create_job replaces it with
        # the complete input-set identity and scopes referenced source units.
        for n, region in enumerate(raw_regions, 1):
            region.update(doc_id=pdf_hash, unit_ids=[f'h{n}'])
        headers = [{'id': f'h{n}', 'page': n, 'source': 'Header', 'kind': 'sentence'} for n in (1, 2)]
        self.job = create_job(self.pdf, self.root / 'compiled-job', headers, regions=raw_regions,
            extra_document={'source_root': str(self.root),
                            'input_files': [str(p) for p in (self.pdf, source, log, manifest)]})
        self.manifest = prepare_ocr(self.job['root'], dpi=144)
        self.responses = []
        for pointer in self.manifest['regions']:
            request = read_json(self.job['root'] / pointer['request'])
            self.responses.append({'region_id': request['region_id'], **{k: request[k] for k in BINDINGS},
                'status': 'transcribed', 'complete': True,
                'producer': {'name': 'fixture transcriber', 'method': 'agent'},
                'units': [{'source': 'Visible fixture text.', 'kind': 'sentence',
                           'bbox': [20, 40, 180, 70], 'order': 0, 'uncertain': False}],
                'uncertainties': []})
        return raw_regions

    def test_reviewed_overlay_supports_pdf_jobs_with_frozen_source_dependencies(self):
        raw_regions = self.create_compiled_source_job()
        doc_id, pdf_hash = self.job['document']['doc_id'], file_hash(self.pdf)
        self.assertNotEqual(doc_id, pdf_hash)
        self.assertEqual(len(self.job['document']['input_files']), 4)
        self.assertEqual(self.manifest['doc_id'], doc_id)
        self.assertEqual(self.manifest['source_sha256'], pdf_hash)
        self.assertTrue(all(r['region_id'].startswith(doc_id[:16] + ':') for r in self.responses))
        self.write()
        self.assertEqual(self.collect()['regions'], 2)
        items, assets = [], []
        regions = copy.deepcopy(raw_regions)
        warnings, info = apply_overlay(self.pdf, self.root / 'overlay.json', items, regions, assets, [])
        self.assertEqual(warnings, [])
        self.assertEqual(info['resolved_regions'], ['p1:image', 'p2:image'])
        self.assertEqual([i['source'] for i in items], ['Visible fixture text.'] * 2)
        self.assertEqual([i['source_region_id'] for i in items], ['p1:image', 'p2:image'])
        self.assertTrue(all(r['disposition'] == 'preserve' for r in regions[:2]))

    def test_changed_frozen_source_dependency_invalidates_reviewed_pdf_overlay(self):
        raw_regions = self.create_compiled_source_job()
        self.write()
        self.collect()
        snapshot = self.job['root'] / 'input/source/main.tex'
        snapshot.write_text('Changed project source after review.', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'Input hash mismatch'):
            load_job(self.job['root'])
        items, assets, regions = [], [], copy.deepcopy(raw_regions)
        with self.assertRaisesRegex(ContractError, 'Input hash mismatch'):
            apply_overlay(self.pdf, self.root / 'overlay.json', items, regions, assets, [])
        self.assertEqual(items, [])
        self.assertEqual(assets, [])
        self.assertEqual(regions, raw_regions)

    def test_preparation_creates_readable_images_without_approvals_or_frozen_edits(self):
        before = {p.name:file_hash(p) for p in self.job['root'].iterdir() if p.is_file()}
        templates = read_jsonl(self.job['root']/'ocr/responses.template.jsonl')
        self.assertEqual([r['status'] for r in templates], ['NOT_RUN', 'NOT_RUN'])
        self.assertFalse((self.job['root']/'ocr/responses.jsonl').exists())
        request = read_json(self.job['root']/self.manifest['regions'][0]['request'])
        self.assertEqual(request['crop_image']['pixel_to_page_points']['scale'], .5)
        self.assertGreater(request['page_image']['width_px'], 200)
        prepare_ocr(self.job['root'], dpi=144)
        self.assertEqual(before, {p.name:file_hash(p) for p in self.job['root'].iterdir() if p.is_file()})

    def test_unread_template_and_partial_or_uncertain_source_cannot_complete(self):
        for changes in ({'status':'NOT_RUN'}, {'complete':False}, {'uncertainties':['unclear exponent']}):
            with self.subTest(changes=changes):
                original=copy.deepcopy(self.responses)
                self.responses[0].update(changes)
                self.write()
                with self.assertRaises(ValueError): self.collect()
                self.assertFalse((self.root/'overlay.json').exists())
                self.responses=original
        self.responses[0]['units'][0]['uncertain']=True
        self.write()
        with self.assertRaisesRegex(ValueError,'Uncertain'): self.collect()

    def test_missing_or_duplicate_region_and_partial_manifest_fail(self):
        self.responses=self.responses[:1]
        self.write()
        with self.assertRaisesRegex(ValueError,'exactly every'): self.collect()
        self.responses.append(copy.deepcopy(self.responses[0]))
        self.write()
        with self.assertRaisesRegex(ValueError,'duplicate'): self.collect()

    def test_wrong_source_or_image_binding_and_changed_image_fail(self):
        self.responses[0]['crop_sha256']='bad'
        self.write()
        with self.assertRaisesRegex(ValueError,'binding'): self.collect()
        request=read_json(self.job['root']/self.manifest['regions'][0]['request'])
        self.responses[0]['crop_sha256']=request['crop_sha256']
        self.write()
        (self.job['root']/request['crop_image']['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'image content changed'): self.collect()

    def test_changed_transcription_requires_another_independent_review(self):
        reviews=self.reviews()
        self.responses[0]['units'][0]['source']='Different claim.'
        self.write(reviews)
        with self.assertRaisesRegex(ValueError,'different transcription'): self.collect()
        reviews=self.reviews()
        reviews[0]['reviewer']['name']='fixture transcriber'
        self.write(reviews)
        with self.assertRaisesRegex(ValueError,'separate source reviewer'): self.collect()

    def test_complete_table_grid_empty_cell_and_figure_crop_survive_overlay(self):
        self.responses[0]['units']=[
            {'source':'Name','kind':'table_cell','bbox':[20,40,90,65], 'table_id':'t1',
             'row':0,'col':0,'table_rows':1,'table_cols':2,'uncertain':False,'order':0},
            {'source':'','kind':'table_cell','bbox':[90,40,180,65], 'table_id':'t1',
             'row':0,'col':1,'table_rows':1,'table_cols':2,'uncertain':False,'order':1},
            {'source':'Figure 1. Red fixture.','kind':'figure','bbox':[20,160,180,185],
             'figure_bbox':[20,80,180,150], 'uncertain':False,'order':2}]
        self.write()
        self.assertEqual(self.collect()['regions'],2)
        items, regions, assets = [], copy.deepcopy(self.regions), []
        warnings, info=apply_overlay(self.pdf,self.root/'overlay.json',items,regions,assets,[])
        self.assertEqual(len(assets),1)
        figure=next(i for i in items if i['kind']=='figure')
        self.assertEqual(assets[0]['owner_ids'],[figure['id']])
        self.assertEqual(figure['asset_id'],assets[0]['asset_id'])
        with Image.open(assets[0]['path']) as image:
            self.assertEqual(image.convert('RGB').getpixel((20,20)),(255,0,0))
        cells=[i for i in items if i['kind']=='table_cell']
        self.assertEqual(cells[0]['table_id'],cells[1]['table_id'])
        self.assertEqual([i['col'] for i in cells],[0,1])
        self.assertEqual(cells[1]['action'],'preserve')
        self.assertTrue(all(r['disposition']=='preserve' for r in regions[:2]))

    def test_missing_cell_or_wrong_figure_crop_is_rejected(self):
        self.responses[0]['units']=[{'source':'Only cell','kind':'table_cell','bbox':[20,40,180,65],
            'table_id':'t','row':0,'col':0,'table_rows':1,'table_cols':2,'uncertain':False}]
        self.write()
        with self.assertRaisesRegex(ValueError,'Incomplete table grid'): self.collect()
        self.responses[0]['units']=[{'source':'Figure 1','kind':'figure','bbox':[20,40,180,65],
            'figure_bbox':[0,0,200,200],'uncertain':False}]
        self.write()
        with self.assertRaisesRegex(ValueError,'outside'): self.collect()

    def test_overlay_application_rechecks_complete_reviewed_payload_before_mutation(self):
        self.write()
        self.collect()
        path = self.root / 'overlay.json'
        baseline = read_json(path)
        self.assertEqual(baseline['schema_version'], 2)
        self.assertEqual(baseline['regions'][0]['response'], self.responses[0])
        def change_units(entry):
            entry['units'][0]['source'] = 'A substituted claim.'
        def change_response(entry):
            entry['response']['units'][0]['source'] = 'A substituted claim.'
            entry['units'] = copy.deepcopy(entry['response']['units'])
            entry['transcription_hash'] = canonical_hash(entry['response'])
        edits = [change_units, change_response,
                 lambda e: e['units'][0].update(bbox=[20,80,180,100]),
                 lambda e: e.update(complete=False),
                 lambda e: e.update(producer={'name':'other','method':'agent'}),
                 lambda e: e.update(transcription_hash='bad'),
                 lambda e: e['response'].update(complete=False),
                 lambda e: e['response'].update(uncertainties=['unclear glyph']),
                 lambda e: e['review'].update(independent=False),
                 lambda e: e['review'].update(status='NOT_RUN'),
                 lambda e: e['review']['reviewer'].update(name=' FIXTURE TRANSCRIBER '),
                 lambda e: e['source_image'].update(bbox_pt=[20,40,180,190]),
                 lambda e: e['request'].update(page=1),
                 lambda e: e.pop('response')]
        edits += [lambda e, key=key: e['review'].update({key:'wrong'}) for key in BINDINGS]
        for edit in edits:
            with self.subTest(edit=edit):
                value = copy.deepcopy(baseline)
                edit(value['regions'][1])
                atomic_write_json(path, value)
                items, regions, assets = [], copy.deepcopy(self.regions), []
                with self.assertRaises(ValueError):
                    apply_overlay(self.pdf, path, items, regions, assets, [])
                self.assertEqual(items, [])
                self.assertEqual(assets, [])
                self.assertEqual(regions, self.regions)
        for edit in (lambda v: v.pop('contract'), lambda v: v.update(schema_version=1),
                     lambda v: v['regions'].pop(), lambda v: v['reviewer'].update(id='other')):
            value = copy.deepcopy(baseline)
            edit(value)
            atomic_write_json(path, value)
            with self.assertRaises(ValueError):
                apply_overlay(self.pdf, path, [], copy.deepcopy(self.regions), [], [])

    def test_overlay_application_rechecks_current_geometry_and_actual_packet_images(self):
        self.write()
        self.collect()
        path = self.root / 'overlay.json'
        regions = copy.deepcopy(self.regions)
        regions[1]['locator']['bbox'][0] += 1
        with self.assertRaisesRegex(ValueError, 'current source extraction'):
            apply_overlay(self.pdf, path, [], regions, [], [])
        request = read_json(self.job['root'] / self.manifest['regions'][1]['request'])
        image = self.job['root'] / request['crop_image']['path']
        image.write_bytes(b'changed after collection')
        with self.assertRaisesRegex(ValueError, 'image content changed'):
            apply_overlay(self.pdf, path, [], copy.deepcopy(self.regions), [], [])

    def test_collect_cannot_overwrite_input_or_evidence_and_identical_retry_is_readonly(self):
        self.write()
        targets = [p for p in self.job['root'].rglob('*') if p.is_file()]
        external_response, external_review = self.root/'responses.jsonl', self.root/'reviews.jsonl'
        atomic_write_jsonl(external_response, self.responses)
        atomic_write_jsonl(external_review, self.reviews())
        for target in targets + [self.pdf, external_response, external_review]:
            with self.subTest(target=target):
                before = file_hash(target)
                with self.assertRaises(ValueError):
                    collect_ocr(self.job['root'], target, external_response, external_review)
                self.assertEqual(file_hash(target), before)
        self.collect()
        overlay = self.root / 'overlay.json'
        before = (file_hash(overlay), overlay.stat().st_mtime_ns)
        self.collect()
        self.assertEqual((file_hash(overlay), overlay.stat().st_mtime_ns), before)
        self.responses[0]['units'][0]['source'] = 'Newly reviewed fixture source.'
        self.write()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.collect()
        self.assertEqual((file_hash(overlay), overlay.stat().st_mtime_ns), before)

    def test_reviewed_reading_order_keeps_table_between_prose_and_native_header(self):
        def unit(ident, y, order=None, kind='sentence'):
            row={'id':ident,'page':1,'kind':kind,'parent_id':ident,
                 'locators':[{'page':1,'bbox':[20,y,180,y+15],'width':200}]}
            if order is not None:
                row.update(provenance='manual',source_region_id='page1-image',source_region_order=order)
            if kind=='table_cell':
                row.update(table_id='table',row=0,col=0)
            return row
        rows=[unit('after',100,3),unit('cell',80,2,'table_cell'),unit('header',5),
              unit('intro1',35,0),unit('intro2',55,1),unit('native-after',185)]
        self.assertEqual([i['id'] for i in order_pdf_units(rows,[])],
                         ['header','intro1','intro2','cell','after','native-after'])
        rows[0]['source_region_order']=2
        with self.assertRaisesRegex(ValueError,'duplicated'): order_pdf_units(rows,[])


if __name__ == '__main__':
    unittest.main()
