"""Opt-in bounded startup routing. No hot rollover, adoption, moves or deletion."""
import os
from pathlib import Path
from . import recorder_receiver_completion as completion
from . import recorder_receiver_publication as publication


def validate(generations, binding, inbox):
    require = publication.require
    require(type(generations) in (list,tuple) and 1 <= len(generations) <= 8,
            'routing_generation_bound')
    result=[]; trees=[]
    for item in generations:
        require(type(item) is dict and set(item)=={'binding','inbox_dir'}, 'routing_generation_fields')
        directory=publication._path(item['inbox_dir'])
        config=publication.validate_binding(item['binding'],directory)
        paths=[Path(config[k]) for k in ('state_dir','staging_dir','inbox_dir')]
        for path in paths:
            require(not any(path.is_relative_to(other) or other.is_relative_to(path) for other in trees),
                    'routing_overlapping_generations')
        trees.extend(paths)
        result.append((item['binding'],directory,Path(config['staging_dir'])))
    matches=[i for i,(b,p,_) in enumerate(result) if b==binding and p==Path(inbox)]
    require(len(matches)==1,'routing_active_generation_missing_or_duplicate')
    return result,matches[0]


class Routes:
    def __init__(self,config):
        self.generations,self.active=validate(config.generations,config.binding,config.inbox_dir)
        self.owners=[]; self.routes={}
        try:
            for binding,inbox,raw in self.generations:
                self.owners.append(completion.ReceiverCompletion(binding=binding,inbox_dir=inbox,
                    exclusive_receiver=True,max_attempts=config.max_attempts,max_raw_bytes=config.max_raw_bytes,
                    deadline_seconds=config.publication_seconds))
            for index,owner in enumerate(self.owners):
                with owner._guard():
                    for name in owner._names:
                        publication.require(name not in self.routes,'routing_attempt_collision')
                        path=self.generations[index][2]/name
                        publication._private(path)
                        self.routes[name]=(index,publication._identity(path))
                # Published-only or unknown names cannot acquire a route by inference.
                inbox=self.generations[index][1]
                with os.scandir(inbox) as entries:
                    for count,entry in enumerate(entries,1):
                        publication.require(count<=config.max_attempts and entry.name in owner._names
                            and entry.is_dir(follow_symlinks=False),'routing_unowned_published_attempt')
        except BaseException:
            self.close();raise

    def _check(self):
        # Leases exclude cooperating owners; detect out-of-band namespace changes
        # in every generation rather than silently route around ambiguity.
        for owner in self.owners:
            with owner._guard(): pass

    def resolve(self,name):
        self._check()
        publication.require(publication.inbox.NAME.fullmatch(name) is not None and name in self.routes,
                            'routing_unknown_attempt')
        index,identity=self.routes[name]
        owner=self.owners[index]
        with owner._guard():
            path=self.generations[index][2]/name
            publication.require(publication._identity(path)==identity,'routing_attempt_changed')
            publication._private(path)
        return index

    def begin(self,name):
        self._check()
        publication.require(name not in self.routes,'routing_existing_attempt')
        index=self.active;owner=self.owners[index]
        attempt=owner.begin_attempt(name)
        self.routes[name]=(index,attempt.identity)
        return owner,attempt

    def close(self):
        error=None
        for owner in reversed(self.owners):
            try:owner.close()
            except BaseException as exc:error=exc
        if error is not None:raise error


def configure(*, endpoint_config, generation_configs, active_generation, output):
    """Create a fresh private config, preserving endpoint policy; never listen."""
    import json
    from . import recorder_ftps_receiver as receiver
    require=publication.require
    require(type(generation_configs) in (list,tuple) and 1<=len(generation_configs)<=8,
            'routing_generation_bound')
    require(type(active_generation) is int and 0<=active_generation<len(generation_configs),
            'routing_active_generation_bound')
    snapshots=[]
    def read(path):
        path=receiver._path(str(path));raw=receiver._private_file(path,16384)
        value=json.loads(raw,object_pairs_hook=receiver._unique_pairs)
        receiver._validate_config(value)
        snapshots.append((path,raw))
        return value
    endpoint=read(endpoint_config);generations=[]
    for path in generation_configs:
        value=read(path)
        require('generations' not in value,'routing_recursive_generation_config')
        require(all(value[k]==endpoint[k] for k in
                    ('username','password_file','cert_file','key_file')),
                'routing_endpoint_identity_mismatch')
        generations.append(dict(binding=value['binding'],inbox_dir=value['inbox_dir']))
    # Retention is additive: changing the active generation cannot erase any
    # namespace advertised by the endpoint we are replacing.
    retained=endpoint.get('generations') or [dict(binding=endpoint['binding'],inbox_dir=endpoint['inbox_dir'])]
    require(all(item in generations for item in retained),'routing_endpoint_generation_omitted')
    data=dict(endpoint,generations=generations,**generations[active_generation])
    config=receiver._validate_config(data)
    entries,_=validate(config.generations,config.binding,config.inbox_dir)
    path=receiver._path(str(output));publication._private(path.parent)
    require(not os.path.lexists(path),'routing_fresh_output_required')
    for binding,inbox,raw in entries:
        original=publication.validate_binding(binding,inbox)
        require(not any(path.is_relative_to(Path(original[k])) for k in
                        ('state_dir','staging_dir','inbox_dir')),'routing_output_inside_data_tree')
    raw=(json.dumps(data,sort_keys=True,indent=2)+'\n').encode()
    require(len(raw)<=16384,'routing_config_byte_bound')
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        identity=os.fstat(directory)
        require(all(receiver._private_file(p,16384)==b for p,b in snapshots),'routing_input_config_changed')
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.fsync(directory)
        require((path.parent.stat().st_dev,path.parent.stat().st_ino)==(identity.st_dev,identity.st_ino),
                'routing_output_parent_changed')
        require(receiver._private_file(path,16384)==raw and
                all(receiver._private_file(p,16384)==b for p,b in snapshots),'routing_config_changed')
        receiver.load_config(path)
    finally:os.close(directory)
    return dict(status='routing_configuration_created',generations=len(generations),
                active_generation=active_generation,network_requests=0,service_started=False,
                hot_rollover_enabled=False)


def main(argv=None):
    import argparse
    import json
    import sys
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint-config',required=True)
    parser.add_argument('--generation-config',dest='generation_configs',action='append',required=True)
    parser.add_argument('--active-generation',type=int,required=True)
    parser.add_argument('--output',required=True)
    try:value=configure(**vars(parser.parse_args(argv)))
    except Exception:
        # Input may contain credentials. Never print raw exception/config data.
        print(json.dumps(dict(status='routing_configuration_refused',preserve_partial_output=True)),file=sys.stderr)
        return 2
    print(json.dumps(value,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
